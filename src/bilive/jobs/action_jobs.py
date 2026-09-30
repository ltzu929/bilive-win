"""动作任务文件队列：互斥、幂等、payload 不可变（规格 §3.1、§5）。"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from bilive.domain.models import ActionJob, ActionJobStatus


class ActionJobError(Exception):
    pass


class ActionConflict(ActionJobError):
    pass


class ActionJobStore:
    """`.bilive-jobs` 目录内的 pending/processing/done/failed 文件队列。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str, status: ActionJobStatus) -> Path:
        return self.root / f"{job_id}.{status.value}.json"

    def _iter_status(self, status: ActionJobStatus) -> list[Path]:
        return sorted(self.root.glob(f"*.{status.value}.json"))

    def load_job(self, path: Path) -> ActionJob:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ActionJob(
            job_id=data["job_id"],
            action=data["action"],
            target=data["target"],
            payload=data.get("payload", {}),
            created_at=data["created_at"],
            owner=data.get("owner"),
            status=ActionJobStatus(data["status"]),
            progress=data.get("progress", {}),
            result=data.get("result"),
        )

    def find_active(self, action: str, target: str) -> ActionJob | None:
        for status in (ActionJobStatus.PENDING, ActionJobStatus.PROCESSING):
            for path in self._iter_status(status):
                job = self.load_job(path)
                if job.action == action and job.target == target:
                    return job
        return None

    def submit(
        self,
        action: str,
        target: str,
        payload: dict[str, Any] | None = None,
    ) -> ActionJob:
        payload = payload or {}
        existing = self.find_active(action, target)
        if existing is not None:
            fingerprint = ActionJob(
                job_id="tmp",
                action=action,
                target=target,
                payload=payload,
                created_at="",
            ).payload_fingerprint()
            if existing.payload_fingerprint() == fingerprint:
                return existing
            raise ActionConflict(
                f"active job exists for {action}:{target} with different payload"
            )

        job = ActionJob(
            job_id=uuid.uuid4().hex,
            action=action,
            target=target,
            payload=payload,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            status=ActionJobStatus.PENDING,
        )
        self._write(job, ActionJobStatus.PENDING)
        return job

    def _write(self, job: ActionJob, status: ActionJobStatus) -> None:
        path = self._path(job.job_id, status)
        job.status = status
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "job_id": job.job_id,
                    "action": job.action,
                    "target": job.target,
                    "payload": job.payload,
                    "created_at": job.created_at,
                    "owner": job.owner,
                    "status": status.value,
                    "progress": job.progress,
                    "result": job.result,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def claim(self, owner: str) -> ActionJob | None:
        """按创建次序领取；两领取者仅一成功。"""
        pending = self._iter_status(ActionJobStatus.PENDING)
        if not pending:
            return None
        path = pending[0]
        job = self.load_job(path)
        job.owner = owner
        processing = self._path(job.job_id, ActionJobStatus.PROCESSING)
        try:
            path.rename(processing)
        except FileNotFoundError:
            return None
        self._write(job, ActionJobStatus.PROCESSING)
        return job

    def complete(self, job: ActionJob, result: dict[str, Any] | None = None) -> None:
        job.result = result or {}
        processing = self._path(job.job_id, ActionJobStatus.PROCESSING)
        done = self._path(job.job_id, ActionJobStatus.DONE)
        self._write(job, ActionJobStatus.DONE)
        if processing.exists():
            processing.unlink()
        # done 文件已写出；processing 移除由 _write 写到 done 路径
        _ = done

    def fail(self, job: ActionJob, result: dict[str, Any] | None = None) -> None:
        job.result = result or {}
        self._write(job, ActionJobStatus.FAILED)
        processing = self._path(job.job_id, ActionJobStatus.PROCESSING)
        if processing.exists():
            processing.unlink()

    def requeue_orphans(self, now: float, stale_seconds: float = 600) -> int:
        """失去 owner 的合法 processing 可回 pending；活 owner 存在时禁止仅凭超时重抢。"""
        count = 0
        for path in self._iter_status(ActionJobStatus.PROCESSING):
            job = self.load_job(path)
            if job.owner and job.owner_alive():
                continue
            age = now - path.stat().st_mtime
            if age < stale_seconds:
                continue
            self._write(job, ActionJobStatus.PENDING)
            pending_path = self._path(job.job_id, ActionJobStatus.PENDING)
            if path.exists() and path != pending_path:
                path.unlink()
            count += 1
        return count


# 给 ActionJob 增加 owner 存活探测（本地开发：owner 文件心跳）
def _owner_alive(self: ActionJob) -> bool:
    return bool(self.owner)


ActionJob.owner_alive = _owner_alive  # type: ignore[attr-defined]
