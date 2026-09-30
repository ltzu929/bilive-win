"""上传队列（规格 §3.3、§5）：staged 不可领取；手动 retry 仅 failed 无 remote_filename。"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from bilive.db.conn import DatabaseUnavailable, open_connection
from bilive.domain.models import UploadStage
from bilive.domain.states import activation_target_stage, manual_retry_allowed


class UploadQueue:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise DatabaseUnavailable(f"database missing: {self.db_path}")
        return open_connection(self.db_path, readonly=False)

    def stage(
        self,
        video_path: str,
        title: str = "",
        description: str = "",
        tags: list[str] | None = None,
    ) -> dict:
        """video_path 唯一暂存 staged。未确认 staged 不可领取。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO upload_queue
                    (video_path, stage, title, description, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_path) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    tags=excluded.tags,
                    updated_at=excluded.updated_at
                """,
                (
                    video_path,
                    UploadStage.STAGED.value,
                    title,
                    description,
                    json.dumps(tags or [], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()
            return self.get_by_path(video_path) or {
                "video_path": video_path,
                "stage": UploadStage.STAGED.value,
            }
        finally:
            conn.close()

    def get_by_path(self, video_path: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM upload_queue WHERE video_path=?", (video_path,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list(self, stage: str | None = None, limit: int = 20, offset: int = 0) -> dict:
        conn = self._conn()
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
            row = conn.execute(
                "SELECT * FROM upload_queue WHERE video_path=?", (video_path,)
            ).fetchone()
            if row is None:
                raise ValueError("upload row not found")
            if row["stage"] not in {
                UploadStage.STAGED.value,
                UploadStage.FAILED.value,
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
            row = conn.execute(
                "SELECT * FROM upload_queue WHERE video_path=?", (video_path,)
            ).fetchone()
            if row is None:
                raise ValueError("upload row not found")
            if row["stage"] in {UploadStage.UPLOADING.value, UploadStage.PUBLISHING.value}:
                raise ValueError(f"cannot withdraw while {row['stage']}")
            if row["stage"] == UploadStage.PUBLISHED.value:
                raise ValueError("published row is immutable")
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
            conn.execute(
                "UPDATE upload_queue SET stage=?, updated_at=?, next_attempt_at=NULL WHERE video_path=?",
                (UploadStage.QUEUED.value, now, video_path),
            )
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

    def mark_published(self, video_path: str, bvid: str) -> dict:
        if not bvid:
            raise ValueError("published requires valid bvid")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE upload_queue SET stage=?, bvid=?, updated_at=?, last_error=NULL WHERE video_path=?",
                (UploadStage.PUBLISHED.value, bvid, now, video_path),
            )
            conn.execute(
                "INSERT INTO upload_history (video_path, stage, detail, created_at) VALUES (?,?,?,?)",
                (video_path, UploadStage.PUBLISHED.value, f"bvid={bvid}", now),
            )
            conn.commit()
            return self.get_by_path(video_path) or {}
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
                WHERE stage=? AND attempts < ?
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY id LIMIT 1
                """,
                (UploadStage.QUEUED.value, max_attempts, now),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                "UPDATE upload_queue SET stage=?, updated_at=? WHERE id=?",
                (UploadStage.UPLOADING.value, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), row["id"]),
            )
            conn.execute("COMMIT")
            return dict(row) | {"stage": UploadStage.UPLOADING.value}
        finally:
            conn.close()
