"""Windows Worker：领取动作任务并执行（规格 §4、§8）。"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from bilive.domain.models import ActionJob
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.finalize import Finalizer, PipelineError
from bilive.upload.queue import UploadQueue
from bilive.worker.locks import LockUnavailable, ProcessLock


class Worker:
    def __init__(
        self,
        jobs: ActionJobStore,
        finalizer: Finalizer,
        lock_path: Path,
        worker_id: str = "worker-1",
        upload_queue: UploadQueue | None = None,
    ) -> None:
        self.jobs = jobs
        self.finalizer = finalizer
        self.lock_path = lock_path
        self.worker_id = worker_id
        self.upload_queue = upload_queue
        self._last_activity = time.time()

    def touch(self) -> None:
        self._last_activity = time.time()

    def idle_too_long(self, timeout: float, busy: bool = False) -> bool:
        """pending/处理/上传等真实工作时不能按空闲退出。"""
        if busy:
            return False
        return (time.time() - self._last_activity) >= timeout

    def execute(self, job: ActionJob) -> dict:
        payload = job.payload or {}
        action = job.action
        self.touch()

        if action in {"finalize", "reburn"}:
            result = self.finalizer.reburn(
                segment_id=job.target,
                source_relpath=payload["source_relpath"],
                source_start=float(payload["source_start"]),
                source_end=float(payload["source_end"]),
                revision=int(payload.get("revision", 1)),
            )
            return {
                "ok": True,
                "final_path": result.final_path,
                "raw_path": result.raw_path,
                "revision": result.revision,
                "requires_reburn": False,
            }

        if action == "render":
            path = self.finalizer.render_only(
                segment_id=job.target,
                source_relpath=payload["source_relpath"],
                source_start=float(payload["source_start"]),
                source_end=float(payload["source_end"]),
            )
            return {"ok": True, "raw_path": str(path)}

        if action == "approve_publish":
            final = self.finalizer.artifact_paths(job.target)["final"]
            if not final.is_file():
                raise PipelineError("cannot approve publish without current final output")
            revision = payload.get("revision")
            meta = self.finalizer.artifact_paths(job.target)["meta"]
            if meta.is_file() and revision is not None:
                saved = json.loads(meta.read_text(encoding="utf-8"))
                if saved.get("revision") != revision:
                    raise PipelineError("approve_publish rejected: stale revision")
            activated: dict | None = None
            if self.upload_queue is not None:
                try:
                    self.upload_queue.stage(str(final))
                    activated = self.upload_queue.activate(str(final))
                except ValueError as exc:
                    raise PipelineError(str(exc)) from exc
            return {
                "ok": True,
                "activate_upload": True,
                "video_path": str(final),
                "has_remote_filename": False,
                "upload_stage": (activated or {}).get("stage"),
            }

        if action == "retry_upload":
            return {"ok": True, "requeued": True, "video_path": job.target}

        raise PipelineError(f"unsupported action: {action}")

    def run_once(self) -> bool:
        """领取并执行一个任务；无任务返回 False。"""
        lock = ProcessLock(self.lock_path)
        try:
            lock.acquire(timeout=0.2)
        except LockUnavailable:
            return False
        try:
            job = self.jobs.claim(self.worker_id)
            if job is None:
                return False
            try:
                result = self.execute(job)
                self.jobs.complete(job, result)
            except Exception as exc:  # noqa: BLE001 - 任务失败必须落 failed 供人工
                self.jobs.fail(
                    job,
                    {"ok": False, "error": str(exc), "trace": traceback.format_exc()[-1500:]},
                )
            self.touch()
            return True
        finally:
            lock.release()

    def run_forever(
        self,
        idle_timeout: float = 900,
        idle_check_interval: float = 30,
        max_jobs: int | None = None,
    ) -> int:
        processed = 0
        last_check = time.time()
        while True:
            did = self.run_once()
            if did:
                processed += 1
                if max_jobs is not None and processed >= max_jobs:
                    return processed
                continue
            now = time.time()
            if now - last_check >= idle_check_interval:
                last_check = now
                if self.idle_too_long(idle_timeout, busy=False):
                    return processed
            time.sleep(0.2)
