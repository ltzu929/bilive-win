"""Native completion event → durable action job → Windows Worker wake.

Adapted from pinned src/server/recording_remux.py; no Pi/SSH wake branch.
This shared subset supports the separate Python 3.10 recorder environment.
"""
from __future__ import annotations
import asyncio
import logging
import re
from contextlib import suppress
from pathlib import Path
from bilive.domain.models import ActionJobStatus, task_id_from_source_relpath

logger = logging.getLogger(__name__)


def queue_completed_recording(videos: Path, jobs, source_path: str):
    root = videos.resolve()
    source = Path(source_path).resolve()
    rel = source.relative_to(root)
    if (source.suffix.lower() != '.flv' or source.stem.endswith('_injecting')
            or not re.match(r'^\d+(?:\s*-|$)', source.parent.name) or not source.is_file()):
        return None
    return jobs.submit('remux_recording', task_id_from_source_relpath(rel.as_posix()), {})


def install_recording_events(app, videos: Path, jobs, worker_control, event_center=None):
    if event_center is None:
        from blrec.event.event_center import EventCenter
        event_center = EventCenter.get_instance()
    wake = asyncio.Event()
    subscription = None
    dispatcher = None
    loop = None

    def on_event(event):
        if event.type != 'VideoPostprocessingCompletedEvent':
            return
        try:
            if queue_completed_recording(videos, jobs, event.data.path):
                loop.call_soon_threadsafe(wake.set)
        except Exception as exc:
            # Media remains untouched. Do not log payload/Cookie or silently drop failures.
            logger.error('Completed recording was not queued: %s', type(exc).__name__)

    async def dispatch():
        while True:
            wake.clear()
            if any(j.action == 'remux_recording' for _, j in jobs.valid_jobs(ActionJobStatus.PENDING)):
                try:
                    await asyncio.to_thread(worker_control.wake)
                except Exception as exc:
                    logger.warning('Persisted remux awaits Worker: %s', type(exc).__name__)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=60)

    async def start():
        nonlocal subscription, dispatcher, loop
        loop = asyncio.get_running_loop()
        subscription = event_center.events.subscribe(on_event)
        dispatcher = asyncio.create_task(dispatch())

    async def stop():
        if subscription is not None:
            subscription.dispose()
        if dispatcher is not None:
            dispatcher.cancel()
            with suppress(asyncio.CancelledError):
                await dispatcher

    # Insert before blrec startup so the earliest completed event is observed.
    app.router.on_startup.insert(0, start)
    app.router.on_shutdown.append(stop)
