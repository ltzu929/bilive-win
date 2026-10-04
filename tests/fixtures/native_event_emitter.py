"""Isolated acceptance helper, run only with the Python 3.10 recorder venv."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))

from fastapi import FastAPI
from blrec.event.event_center import EventCenter
from blrec.event.models import VideoPostprocessingCompletedEvent, VideoPostprocessingCompletedEventData
from bilive.jobs.action_jobs import ActionJobStore
from bilive.recorder.events import install_recording_events


async def main():
    videos,jobs_root=map(Path,sys.argv[1:3])
    if not (videos.parent/'.bilive-isolated-acceptance').is_file():
        raise RuntimeError('isolated acceptance marker required')
    center=EventCenter.get_instance();app=FastAPI();wakes=[]
    jobs=ActionJobStore(jobs_root)
    install_recording_events(app,videos,jobs,SimpleNamespace(wake=lambda:wakes.append(True)),center)
    await app.router.startup()
    try:
        for path in sys.argv[3:]:
            source=Path(path).resolve()
            if not source.is_relative_to(videos.resolve()):
                raise RuntimeError('fixture source outside isolated root')
            room=int(source.parent.name.split(' ')[0])
            event=VideoPostprocessingCompletedEvent.from_data(VideoPostprocessingCompletedEventData(room,str(source)))
            center.submit(event);center.submit(event)  # Native duplicate delivery.
        for _ in range(100):
            if wakes:break
            await asyncio.sleep(.01)
        assert wakes and len(jobs.valid_jobs(__import__('bilive.domain.models',fromlist=['ActionJobStatus']).ActionJobStatus.PENDING)) == len(sys.argv[3:])
        print('NATIVE_EVENT_CENTER_PASS Python3.10 native events/duplicate delivery/durable jobs/async wake')
    finally:
        await app.router.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
