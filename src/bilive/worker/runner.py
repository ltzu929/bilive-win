"""Windows Worker：领取动作任务并执行（规格 §4、§8）。"""

from __future__ import annotations

import json
import time
import uuid
import threading
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
        workbench=None,
        preflight=None,
        analysis=None,
        lifecycle=None,
        auto_analysis_enabled=None,
    ) -> None:
        self.jobs = jobs
        self.finalizer = finalizer
        self.lock_path = lock_path
        self.worker_id = f"{worker_id}-{uuid.uuid4().hex}"
        self.upload_queue = upload_queue
        self.workbench = workbench
        if preflight is None:
            from bilive.worker.preflight import check_action
            preflight = check_action
        self.preflight = preflight
        self.analysis = analysis
        self.lifecycle = lifecycle
        self.auto_analysis_enabled = auto_analysis_enabled or (lambda: False)
        self._owner_lease = None
        self.stop_event = threading.Event()
        self._last_activity = time.time()
        self.external_busy = lambda: False
        self.on_tick = lambda: None

    def stop(self) -> None:
        """Finish the current action, then leave remaining tasks persisted."""
        self.stop_event.set()
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
        if action in {'trash_source', 'force_expired'}:
            if self.lifecycle is None:
                raise PipelineError('source lifecycle unavailable')
            return self.lifecycle.execute(job)
        if action == "start_analysis":
            if payload.get('automatic') and self.auto_analysis_enabled() is not True:
                from bilive.domain.models import source_relpath_from_task_id
                source = self.workbench.source(source_relpath_from_task_id(job.target))
                source.with_suffix('.mp4.pending').unlink(missing_ok=True)
                return {'ok':True, 'cancelled':True, 'reason':'automatic analysis disabled'}
            if self.analysis is None:
                raise PipelineError("analysis pipeline unavailable")
            from bilive.analysis.tasks import RecordingTasks
            return RecordingTasks(self.workbench).execute(job, self.analysis)
        if action == 'retry_judge':
            if self.analysis is None:
                raise PipelineError('analysis pipeline unavailable')
            return self.analysis.retry_judge(job)
        if action == 'transcribe_subtitles':
            if self.analysis is None:
                raise PipelineError('local transcription unavailable')
            return self.analysis.transcribe_segment(job)
        if action == 'generate_metadata':
            if self.analysis is None:
                raise PipelineError('文案生成不可用')
            return self.analysis.generate_segment_metadata(job)
        if action == "remux_recording":
            from bilive.media.remux import remux_recording
            from bilive.domain.models import source_relpath_from_task_id
            rel = source_relpath_from_task_id(job.target)
            path = (self.finalizer.videos_root / rel).resolve()
            if not path.is_relative_to(self.finalizer.videos_root.resolve()) or path.suffix.lower() != ".flv":
                raise PipelineError("invalid remux source")
            result = remux_recording(path)
            # Recording completion only prepares local playback. AI analysis
            # is opt-in; the setting is refreshed even for an existing Worker.
            analysis_job = None
            if self.workbench is not None and self.auto_analysis_enabled() is True:
                from bilive.analysis.tasks import RecordingTasks
                from bilive.domain.models import task_id_from_source_relpath
                mp4_rel = Path(result.mp4_path).relative_to(self.workbench.videos).as_posix()
                analysis_job = RecordingTasks(self.workbench).queue(task_id_from_source_relpath(mp4_rel), automatic=True).job_id
            return {"ok":True,"mp4_path":result.mp4_path,"verified":result.verified,
                    'analysis_job_id':analysis_job}
        if self.workbench is not None and action in {"finalize", "render", "reburn", "approve_publish"}:
            return self.workbench.execute(job, self.finalizer)

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
            if self.upload_queue is None:
                raise PipelineError("upload queue unavailable")
            row = self.upload_queue.manual_retry(job.target)
            return {"ok": True, "requeued": True, "video_path": job.target, "stage": row["stage"]}

        raise PipelineError(f"unsupported action: {action}")

    def run_once(self) -> bool:
        """领取并执行一个任务；无任务返回 False。"""
        lock = ProcessLock(self.lock_path)
        try:
            lock.acquire(timeout=0.2)
        except LockUnavailable:
            return False
        try:
            owner_lock = None
            if self._owner_lease is None:
                owner_lock = ProcessLock(self.jobs.root / "owners" / f"{self.worker_id}.lock")
                owner_lock.acquire()
            self.jobs.requeue_orphans(time.time(), stale_seconds=0)
            job = self.jobs.claim(self.worker_id, self.preflight)
            if job is None:
                return False
            try:
                result = self.execute(job)
                self.jobs.complete(job, result)
            except Exception as exc:  # noqa: BLE001 - 任务失败必须落 failed 供人工
                from bilive.config.secrets import redact
                self.jobs.fail(
                    job,
                    {"ok": False, "error": redact(str(exc))},
                )
            self.touch()
            return True
        finally:
            if 'owner_lock' in locals() and owner_lock is not None:
                owner_lock.release()
            lock.release()

    def run_forever(
        self,
        idle_timeout: float = 900,
        idle_check_interval: float = 30,
        max_jobs: int | None = None,
    ) -> int:
        instance = ProcessLock(self.lock_path.with_suffix(".instance.lock"))
        instance.acquire()
        self._owner_lease = ProcessLock(self.jobs.root / "owners" / f"{self.worker_id}.lock")
        try:
            self._owner_lease.acquire()
            return self._drain(idle_timeout, idle_check_interval, max_jobs)
        finally:
            self._owner_lease.release()
            self._owner_lease = None
            instance.release()

    def _drain(self, idle_timeout, idle_check_interval, max_jobs):
        processed = 0
        last_check = time.time()
        while not self.stop_event.is_set():
            self.on_tick()
            did = self.run_once()
            if did:
                processed += 1
                if max_jobs is not None and processed >= max_jobs:
                    return processed
                continue
            now = time.time()
            if now - last_check >= idle_check_interval:
                last_check = now
                if self.idle_too_long(idle_timeout, busy=self.jobs.busy() or self.external_busy()):
                    return processed
            self.stop_event.wait(0.2)
        return processed
