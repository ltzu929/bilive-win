"""动作任务文件队列：互斥、幂等、payload 不可变（规格 §3.1、§5）。"""

from __future__ import annotations

import json
import os
import time
import uuid
import re
from pathlib import Path
from typing import Any
from functools import wraps


def queue_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        from bilive.worker.locks import ProcessLock
        with ProcessLock(self.root / "queue.lock"):
            return method(self, *args, **kwargs)
    return locked

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
        if not isinstance(data, dict) or not re.fullmatch(r"[a-f0-9]{32}", str(data.get("job_id", ""))):
            raise ValueError("invalid job identity")
        if path.name != f"{data['job_id']}.{data.get('status')}.json":
            raise ValueError("job filename and status disagree")
        if not isinstance(data.get("payload", {}), dict) or not isinstance(data.get("created_at"), str):
            raise ValueError("invalid job payload or creation time")
        if not isinstance(data.get("action"), str) or not isinstance(data.get("target"), str):
            raise ValueError("invalid action target")
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

    def valid_jobs(self, status: ActionJobStatus, quarantine: bool = False) -> list[tuple[Path, ActionJob]]:
        valid = []
        for path in self._iter_status(status):
            try:
                valid.append((path, self.load_job(path)))
            except FileNotFoundError:
                # Another execution lane may have just moved this job.
                continue
            except (ValueError, KeyError, TypeError):
                if quarantine:
                    folder = self.root / "quarantine"
                    folder.mkdir(exist_ok=True)
                    path.replace(folder / f"{uuid.uuid4().hex}-{path.name}")
        return valid

    def find_active(self, action: str, target: str) -> ActionJob | None:
        for status in (ActionJobStatus.PENDING, ActionJobStatus.PROCESSING):
            for path, job in self.valid_jobs(status, quarantine=True):
                if job.target == target:
                    return job
        return None

    @queue_locked
    def submit(
        self,
        action: str,
        target: str,
        payload: dict[str, Any] | None = None,
        expired_retention: bool = False,
    ) -> ActionJob:
        payload = payload or {}
        existing = self.find_active(action, target)
        if expired_retention:
            if action != 'force_expired':
                raise ValueError('retention exception only applies to force_expired')
            # Expired source packages may be queued despite unfinished analysis.
            # The single Worker still serializes execution; trash tasks stay mutually exclusive.
            existing = next((j for state in (ActionJobStatus.PENDING, ActionJobStatus.PROCESSING)
                             for _, j in self.valid_jobs(state)
                             if j.target == target and j.action in {'trash_source', 'force_expired'}), None)
        if existing is not None:
            if existing.action != action:
                raise ActionConflict("conflicting action for the same target")
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

    def claim(self, owner: str, preflight=None, *, action_filter=None) -> ActionJob | None:
        """按创建次序领取；两领取者仅一成功。"""
        from bilive.worker.locks import ProcessLock
        with ProcessLock(self.root / 'queue.lock'):
            pending = sorted(self.valid_jobs(ActionJobStatus.PENDING, quarantine=True),
                             key=lambda item: (item[1].created_at, item[0].stat().st_mtime_ns))
        for path, job in pending:
            if action_filter is not None and not action_filter(job.action):
                continue
            # Loading a local GPU model may take seconds. Keep submissions and
            # cancellation available, then recheck the durable state before claim.
            ready, reason = preflight(job) if preflight else (True, "")
            with ProcessLock(self.root / 'queue.lock'):
                if not path.is_file() or any(self._path(job.job_id,state).is_file() for state in
                        (ActionJobStatus.PROCESSING,ActionJobStatus.DONE,ActionJobStatus.FAILED)):
                    continue
                current = self.load_job(path)
                if current.payload_fingerprint() != job.payload_fingerprint():
                    continue
                if not ready:
                    progress = {"phase": "blocked", "reason": reason}
                    if current.progress != progress:
                        current.progress = progress
                        self._write(current, ActionJobStatus.PENDING)
                    continue
                current.progress = {"phase": "processing", "started_at":time.time()}
                current.owner = owner
                self._write(current, ActionJobStatus.PROCESSING)
                path.unlink()
                return current
        return None

    @queue_locked
    def cancel_pending(self, job_id: str) -> None:
        if self._path(job_id, ActionJobStatus.PROCESSING).exists():
            raise ActionConflict('processing task cannot be cancelled')
        path = self._path(job_id, ActionJobStatus.PENDING)
        if not path.is_file():
            raise ActionConflict('pending task is no longer available')
        job = self.load_job(path)
        job.result = {'ok':True,'cancelled':True}
        self._write(job, ActionJobStatus.DONE)
        path.unlink()

    @queue_locked
    def complete(self, job: ActionJob, result: dict[str, Any] | None = None) -> None:
        job.result = result or {}
        self.finish_progress(job,'done')
        processing = self._path(job.job_id, ActionJobStatus.PROCESSING)
        done = self._path(job.job_id, ActionJobStatus.DONE)
        self._write(job, ActionJobStatus.DONE)
        if processing.exists():
            processing.unlink()
        # done 文件已写出；processing 移除由 _write 写到 done 路径
        _ = done

    @queue_locked
    def fail(self, job: ActionJob, result: dict[str, Any] | None = None) -> None:
        job.result = result or {}
        self.finish_progress(job,'failed')
        self._write(job, ActionJobStatus.FAILED)
        processing = self._path(job.job_id, ActionJobStatus.PROCESSING)
        if processing.exists():
            processing.unlink()

    @staticmethod
    def finish_progress(job,phase):
        finished=time.time();started=job.progress.get('started_at')
        job.progress={**job.progress,'phase':phase,'finished_at':finished}
        if isinstance(started,(int,float)):
            job.progress['elapsed_seconds']=round(max(0,finished-started),3)

    def owner_alive(self, owner: str | None) -> bool:
        from bilive.worker.locks import ProcessLock, LockUnavailable
        if not owner or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", owner):
            return False
        lock = ProcessLock(self.root / "owners" / f"{owner}.lock", create=False)
        try:
            lock.acquire()
        except FileNotFoundError:
            return False
        except LockUnavailable:
            return True
        else:
            lock.release()
            return False

    def busy(self) -> bool:
        return any(self.valid_jobs(status) for status in (ActionJobStatus.PENDING, ActionJobStatus.PROCESSING))

    @queue_locked
    def requeue_orphans(self, now: float, stale_seconds: float = 600) -> int:
        """失去 owner 的合法 processing 可回 pending；活 owner 存在时禁止仅凭超时重抢。"""
        count = 0
        # Claim/result writes precede removal of the old status file. A crash
        # between them may leave pending beside a terminal result; never rerun it
        # or let that leftover keep the Worker permanently busy.
        for path, job in self.valid_jobs(ActionJobStatus.PENDING, quarantine=True):
            if any(self._path(job.job_id,status).is_file() for status in
                   (ActionJobStatus.DONE,ActionJobStatus.FAILED)):
                path.unlink()
        for path, job in self.valid_jobs(ActionJobStatus.PROCESSING, quarantine=True):
            terminal = any(self._path(job.job_id, status).is_file()
                           for status in (ActionJobStatus.DONE, ActionJobStatus.FAILED))
            if terminal:
                path.unlink()
                continue
            if self.owner_alive(job.owner):
                continue
            age = now - path.stat().st_mtime
            if age < stale_seconds:
                continue
            job.owner = None
            job.progress = {"phase": "recovered"}
            self._write(job, ActionJobStatus.PENDING)
            pending_path = self._path(job.job_id, ActionJobStatus.PENDING)
            if path.exists() and path != pending_path:
                path.unlink()
            count += 1
        return count
