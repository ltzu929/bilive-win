"""上传队列（规格 §3.3、§5）：staged 不可领取；手动 retry 仅 failed 无 remote_filename。"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from bilive.db.conn import DatabaseUnavailable, open_connection, require_table, migrate_upload_columns
from bilive.domain.models import UploadStage
from bilive.domain.states import activation_target_stage, manual_retry_allowed


class UploadQueue:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _conn(self, *, readonly: bool = False) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise DatabaseUnavailable(f"database missing: {self.db_path}")
        conn = open_connection(self.db_path, readonly=readonly)
        try:
            require_table(conn, 'upload_queue')
            require_table(conn, 'upload_history')
            if not readonly:
                migrate_upload_columns(conn)
        except Exception:
            conn.close()
            raise
        return conn

    def remove_staged(self, video_path: str) -> None:
        """Atomically withdraw an unapproved artifact before an edit."""
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM upload_queue WHERE video_path=?", (video_path,)).fetchone()
            if row is None or row["stage"] != "staged" or row["remote_filename"]:
                raise ValueError("final is protected or its queue state is unknown")
            conn.execute("DELETE FROM upload_queue WHERE video_path=?", (video_path,))
            conn.commit()
        finally:
            conn.close()

    def stage(
        self,
        video_path: str,
        title: str = "",
        description: str = "",
        tags: list[str] | None = None,
        *, tid: int | None = None, cover_path: str | None = None, topic: dict | None = None, season: dict | None = None,
    ) -> dict:
        """video_path 唯一暂存 staged。未确认 staged 不可领取。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        from bilive.vendor.upload.publication_options import validate_selections
        validate_selections(topic, season)
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO upload_queue
                    (video_path, stage, title, description, tags, created_at, updated_at, tid, cover_path, topic, season)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_path) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    tags=excluded.tags,
                    tid=COALESCE(excluded.tid,upload_queue.tid),
                    cover_path=COALESCE(excluded.cover_path,upload_queue.cover_path),
                    topic=excluded.topic,
                    season=excluded.season,
                    updated_at=excluded.updated_at
                WHERE upload_queue.stage='staged' AND upload_queue.remote_filename IS NULL
                """,
                (
                    video_path,
                    UploadStage.STAGED.value,
                    title,
                    description,
                    json.dumps(tags or [], ensure_ascii=False),
                    now,
                    now,
                    tid,
                    cover_path,
                    json.dumps(topic, ensure_ascii=False) if topic else None,
                    json.dumps(season, ensure_ascii=False) if season else None,
                ),
            )
            if cur.rowcount == 0:
                raise ValueError('protected upload cannot be restaged or have metadata replaced')
            conn.commit()
            return self.get_by_path(video_path) or {
                "video_path": video_path,
                "stage": UploadStage.STAGED.value,
            }
        finally:
            conn.close()

    def get_by_path(self, video_path: str) -> dict | None:
        conn = self._conn(readonly=True)
        try:
            row = conn.execute(
                "SELECT * FROM upload_queue WHERE video_path=?", (video_path,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list(self, stage: str | None = None, limit: int = 20, offset: int = 0) -> dict:
        conn = self._conn(readonly=True)
        try:
            if stage is None:
                where = "WHERE stage != ?"
                params: list = [UploadStage.STAGED.value]
            else:
                where = "WHERE stage = ?"
                params = [stage]
            total = conn.execute(
                f"SELECT COUNT(*) FROM upload_queue {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM upload_queue {where} ORDER BY id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            return {
                "items": [dict(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        finally:
            conn.close()

    def activate(self, video_path: str, expected_revision: int | None = None) -> dict:
        """人工批准当前有效版本后激活。

        已有 remote_filename 的 staged 行可直接进入 uploaded。
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM upload_queue WHERE video_path=?", (video_path,)
            ).fetchone()
            if row is None:
                raise ValueError("upload row not found")
            if row["stage"] not in {
                UploadStage.STAGED.value,
            }:
                raise ValueError(f"cannot activate from stage={row['stage']}")
            remote = row["remote_filename"]
            target = activation_target_stage(bool(remote))
            conn.execute(
                "UPDATE upload_queue SET stage=?, updated_at=?, last_error=NULL WHERE video_path=?",
                (target, now, video_path),
            )
            conn.execute(
                "INSERT INTO upload_history (video_path, stage, detail, created_at) VALUES (?,?,?,?)",
                (video_path, target, "activated", now),
            )
            conn.commit()
            return self.get_by_path(video_path) or {}
        finally:
            conn.close()

    def withdraw(self, video_path: str) -> dict:
        """字幕/样式/范围修改后撤回可撤回的 staged 行。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self._conn()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                "SELECT * FROM upload_queue WHERE video_path=?", (video_path,)
            ).fetchone()
            if row is None:
                raise ValueError("upload row not found")
            if row["stage"] in {UploadStage.UPLOADING.value, UploadStage.PUBLISHING.value}:
                raise ValueError(f"cannot withdraw while {row['stage']}")
            if row["stage"] == UploadStage.PUBLISHED.value:
                raise ValueError("published row is immutable")
            if row['stage'] == 'cancelled':
                raise ValueError('已取消的任务不能自动撤回为待发布')
            conn.execute(
                "UPDATE upload_queue SET stage=?, updated_at=? WHERE video_path=?",
                (UploadStage.STAGED.value, now, video_path),
            )
            conn.execute(
                "INSERT INTO upload_history (video_path, stage, detail, created_at) VALUES (?,?,?,?)",
                (video_path, UploadStage.STAGED.value, "withdrawn", now),
            )
            conn.commit()
            return self.get_by_path(video_path) or {}
        finally:
            conn.close()

    def cancel(self, video_path: str) -> dict:
        """Cancel local future work without deleting artifacts or remote identifiers."""
        conn = self._conn()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM upload_queue WHERE video_path=?', (video_path,)).fetchone()
            if row is None:
                raise ValueError('未找到该上传任务')
            if row['stage'] == 'cancelled':
                conn.rollback()
                return dict(row)
            if row['stage'] in {'uploading', 'publishing'}:
                raise ValueError('任务正在上传或投稿，请等待当前操作结束后再取消')
            if row['stage'] not in {'staged', 'queued', 'uploaded', 'failed'}:
                raise ValueError('该任务已发布或状态不可取消；取消本地任务不能撤回 B 站稿件')
            now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            conn.execute("UPDATE upload_queue SET stage='cancelled',next_attempt_at=NULL,updated_at=? WHERE video_path=?",
                         (now, video_path))
            conn.execute('INSERT INTO upload_history (video_path,stage,detail,created_at) VALUES (?,?,?,?)',
                         (video_path, 'cancelled', '用户取消本地上传及发布任务，保留成片与远端结果', now))
            conn.commit()
            return self.get_by_path(video_path) or {}
        finally:
            conn.close()

    def manual_retry(self, video_path: str) -> dict:
        row = self.get_by_path(video_path)
        if row is None:
            raise ValueError("upload row not found")
        if not manual_retry_allowed(row["stage"], row.get("remote_filename")):
            raise ValueError(
                "manual retry only for failed rows without remote_filename"
            )
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self._conn()
        try:
            cur = conn.execute(
                "UPDATE upload_queue SET stage=?, updated_at=?, next_attempt_at=NULL,attempts=0 WHERE video_path=? AND stage='failed' AND (remote_filename IS NULL OR remote_filename='')",
                (UploadStage.QUEUED.value, now, video_path),
            )
            if cur.rowcount != 1:
                raise ValueError('任务状态已变化，无法重试')
            conn.commit()
            return self.get_by_path(video_path) or {}
        finally:
            conn.close()

    def mark_uploaded(self, video_path: str, remote_filename: str) -> dict:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE upload_queue SET stage=?, remote_filename=?, updated_at=?, last_error=NULL WHERE video_path=?",
                (UploadStage.UPLOADED.value, remote_filename, now, video_path),
            )
            conn.commit()
            return self.get_by_path(video_path) or {}
        finally:
            conn.close()

    def mark_published(self, video_path: str, bvid: str, aid: int | None = None) -> dict:
        if not bvid:
            raise ValueError("published requires valid bvid")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE upload_queue SET stage=?, bvid=?, aid=COALESCE(?,aid), updated_at=?, published_at=COALESCE(published_at,?), "
                "last_error=NULL, season_status=CASE WHEN season IS NOT NULL THEN COALESCE(season_status,'pending') END WHERE video_path=?",
                (UploadStage.PUBLISHED.value, bvid, aid, now, time.time(), video_path),
            )
            conn.execute(
                "INSERT INTO upload_history (video_path, stage, detail, created_at) VALUES (?,?,?,?)",
                (video_path, UploadStage.PUBLISHED.value, f"bvid={bvid}", now),
            )
            conn.commit()
            return self.get_by_path(video_path) or {}
        finally:
            conn.close()

    def save_season_result(self, video_path: str, error: str | None = None) -> None:
        conn = self._conn()
        try:
            changed = conn.execute("UPDATE upload_queue SET season_status=?,season_error=? "
                                   "WHERE video_path=? AND stage='published' AND season IS NOT NULL",
                                   ('failed' if error else 'joined', error, video_path)).rowcount
            if changed != 1:
                raise ValueError('仅已投稿稿件可以更新合集结果')
            conn.commit()
        finally:
            conn.close()

    def mark_publishing(self, video_path: str) -> None:
        conn = self._conn()
        try:
            changed = conn.execute("UPDATE upload_queue SET stage='publishing' WHERE video_path=? "
                                   "AND stage IN ('uploaded','publishing') AND remote_filename IS NOT NULL",
                                   (video_path,)).rowcount
            if changed != 1:
                raise ValueError("media upload not confirmed")
            conn.commit()
        finally:
            conn.close()

    def save_cover_url(self, video_path: str, url: str) -> None:
        conn = self._conn()
        try:
            conn.execute('UPDATE upload_queue SET cover_url=? WHERE video_path=?', (url, video_path))
            conn.commit()
        finally:
            conn.close()

    def recent_published(self, paths: list[str], limit: int = 5) -> list[dict]:
        if not paths:
            return []
        conn = self._conn(readonly=True)
        try:
            rows = conn.execute(f"SELECT * FROM upload_queue WHERE stage='published' AND bvid IS NOT NULL "
                                f"AND video_path IN ({','.join('?' for _ in paths)}) ORDER BY updated_at DESC LIMIT ?",
                                [*paths, limit]).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def mark_failed(self, video_path: str, error: str, has_remote: bool = False) -> dict:
        """可重试失败按 next_attempt_at 回到 queued 或 uploaded；此处先落 failed。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE upload_queue SET stage=?, last_error=?, updated_at=?, attempts=attempts+1 WHERE video_path=?",
                (UploadStage.FAILED.value, error, now, video_path),
            )
            conn.commit()
            return self.get_by_path(video_path) or {}
        finally:
            conn.close()

    def claim_next(self, max_attempts: int = 3) -> dict | None:
        """消费者领取：仅 queued，且 attempts < max；短原子事务。"""
        now = time.time()
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM upload_queue
                WHERE stage IN (?, ?) AND attempts < ?
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY id LIMIT 1
                """,
                (UploadStage.QUEUED.value, UploadStage.UPLOADED.value, max_attempts, now),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                "UPDATE upload_queue SET stage=?, updated_at=? WHERE id=?",
                ("publishing" if row["remote_filename"] else "uploading", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), row["id"]),
            )
            conn.execute("COMMIT")
            return dict(row) | {"stage": "publishing" if row["remote_filename"] else "uploading"}
        finally:
            conn.close()

    def peek_next(self, now: float | None = None) -> dict | None:
        conn = self._conn(readonly=True)
        try:
            row = conn.execute("SELECT * FROM upload_queue WHERE stage IN ('queued','uploaded') "
                               "AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY id LIMIT 1",
                               (time.time() if now is None else now,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def recover_interrupted(self) -> int:
        """Caller must own the consumer lock before recovering queue state."""
        conn = self._conn()
        try:
            cur = conn.execute("UPDATE upload_queue SET stage=CASE WHEN remote_filename IS NULL OR remote_filename='' "
                               "THEN 'queued' ELSE 'uploaded' END WHERE stage IN ('uploading','publishing')")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def defer_auth(self, video_path: str, next_attempt: float) -> None:
        conn = self._conn()
        try:
            conn.execute("UPDATE upload_queue SET next_attempt_at=?,last_error='authentication unavailable' WHERE video_path=? AND stage IN ('queued','uploaded')",
                         (next_attempt, video_path))
            conn.commit()
        finally:
            conn.close()

    def schedule_retry(self, video_path: str, error: str, max_attempts: int, delay: float, now: float) -> dict:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM upload_queue WHERE video_path=?", (video_path,)).fetchone()
            if row is None:
                raise ValueError("upload row missing")
            attempts = row["attempts"] + 1
            stage = "failed" if attempts >= max_attempts else ("uploaded" if row["remote_filename"] else "queued")
            conn.execute("UPDATE upload_queue SET stage=?,attempts=?,last_error=?,next_attempt_at=? WHERE video_path=?",
                         (stage,attempts,error,None if stage == "failed" else now+delay,video_path))
            conn.commit()
            return {"stage":stage,"attempts":attempts}
        finally:
            conn.close()
