"""Approval must reach the upload queue before unrelated heavy work finishes."""
import threading
import time
from types import SimpleNamespace

import pytest

from bilive.db.conn import init_upload_db
from bilive.domain.models import ActionJobStatus, SubtitleLine, task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionConflict, ActionJobStore
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench
from bilive.upload.queue import UploadQueue
from bilive.worker.runner import Worker


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    assert predicate(), 'condition did not become true before heavy work was released'


@pytest.fixture
def workbench(tmp_path):
    videos = tmp_path / 'Videos'
    videos.mkdir()
    jobs = ActionJobStore(tmp_path / 'jobs')
    artifacts = tmp_path / 'artifacts'
    subtitles = SubtitleStore(artifacts / 'subtitles')
    db = tmp_path / 'uploads.db'
    init_upload_db(db)
    return Workbench(videos, artifacts, jobs, subtitles, UploadQueue(db))


def staged_segment(wb, sid='ready'):
    (wb.videos / f'{sid}.mp4').write_bytes(b'isolated source')
    final = wb.artifacts / sid / 'final.mp4'
    final.parent.mkdir(parents=True)
    final.write_bytes(b'isolated final: approval does not decode or re-encode it')
    wb.subtitles.save(sid, 2, [SubtitleLine(1, 0, 1, 'saved subtitle')], 1)
    wb.uploads.stage(str(final), title=sid)
    segment = dict(segment_id=sid, source_relpath=f'{sid}.mp4', source_start=0,
                   source_end=2, title=sid, revision=1, subtitles_revision=1,
                   final_revision=1, final_path=str(final), publish_approval=False,
                   state='awaiting_publish', decision='manual_keep')
    wb.save(segment)
    return segment


@pytest.mark.parametrize('blocking_phase', ['analysis', 'preflight', 'reburn', 'retry_judge'])
def test_approval_and_upload_tick_continue_during_heavy_work(workbench, tmp_path, monkeypatch, blocking_phase):
    wb = workbench
    ready = staged_segment(wb)
    other = staged_segment(wb, 'other')
    entered, release, upload_tick = threading.Event(), threading.Event(), threading.Event()
    allow_approval = threading.Event()
    errors = []

    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(10), 'test did not release heavy work'
        return {'ok': True}

    pipeline = SimpleNamespace(settings=SimpleNamespace(video=SimpleNamespace(min_source_size_mb=0)), run=blocking)
    def reburn(*args):
        blocking()
        return SimpleNamespace(final_path=other['final_path'])
    finalizer = SimpleNamespace(reburn=reburn)
    if blocking_phase == 'retry_judge':
        from bilive.analysis.pipeline import AnalysisPipeline
        from bilive.config.settings import Settings
        settings = Settings()
        settings.multi_modal.unload_audio_model_after_analysis = False
        pipeline = AnalysisPipeline(settings, wb, finalizer)
        candidate = wb.artifacts / 'candidates' / task_id_from_source_relpath('other.mp4') / 'candidate.mp4'
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b'isolated candidate')
        (wb.videos / 'other.xml').write_text('<i/>')
        other.update(candidate_path=str(candidate), candidate_start=0, candidate_end=2,
                     core_start=0, core_end=1)
        wb.save(other)
        def prepare(*args):
            blocking()
            return {'results': []}
        monkeypatch.setattr(pipeline, '_prepare', prepare)
    def preflight(job):
        if job.action == 'approve_publish':
            assert allow_approval.wait(5)
        if blocking_phase == 'preflight' and job.action == 'start_analysis':
            blocking()
        return True, ''

    worker = Worker(wb.jobs, finalizer, tmp_path / 'worker.lock', workbench=wb,
                    upload_queue=wb.uploads, analysis=pipeline, preflight=preflight)
    def tick():
        if wb.uploads.get_by_path(ready['final_path'])['stage'] == 'queued':
            upload_tick.set()
    worker.on_tick = tick
    # Cover generation is unrelated to scheduling and needs valid decoded media.
    monkeypatch.setattr('bilive.media.publication.generate_default_cover', lambda path: path.with_suffix('.jpg'))
    if blocking_phase in {'reburn', 'retry_judge'}:
        heavy = wb.submit(blocking_phase, other['segment_id'], 1)
    else:
        heavy = {'job_id': wb.jobs.submit('start_analysis', task_id_from_source_relpath('other.mp4')).job_id}
    def run():
        try:
            worker.run_forever(idle_timeout=30)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ActionConflict):
            wb.submit('approve_publish', other['segment_id'], 1)
        waiting_heavy = wb.jobs.submit('render', 'future')
        approval = wb.submit('approve_publish', ready['segment_id'], 1)
        # Duplicate clicks still resolve to the same durable job.
        assert wb.submit('approve_publish', ready['segment_id'], 1)['job_id'] == approval['job_id']
        allow_approval.set()
        wait_until(lambda: (wb.jobs.root / f"{approval['job_id']}.done.json").is_file())
        assert not release.is_set()
        assert not (wb.jobs.root / f"{heavy['job_id']}.done.json").exists()
        assert (wb.jobs.root / f'{waiting_heavy.job_id}.pending.json').exists()
        assert wb.load(ready['segment_id'])['publish_approval']
        assert wb.uploads.get_by_path(ready['final_path'])['stage'] == 'queued'
        assert upload_tick.wait(2), 'upload supervision was blocked behind analysis'
        assert wb.jobs.requeue_orphans(time.time() + 99999, stale_seconds=0) == 0
        with pytest.raises(ActionConflict):
            wb.save_metadata(ready['segment_id'], 1, 'forbidden edit', '', [])
    finally:
        worker.stop()
        allow_approval.set()
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert (wb.jobs.root / f"{heavy['job_id']}.done.json").is_file()


def test_stale_approval_fails_without_activating_upload(workbench, tmp_path):
    wb = workbench
    segment = staged_segment(wb)
    # Simulate a persisted stale request; execution must recheck its revision.
    job = wb.jobs.submit('approve_publish', segment['segment_id'], {'revision': 0})
    worker = Worker(wb.jobs, None, tmp_path / 'worker.lock', workbench=wb)
    assert worker.run_once(approval_only=True)
    failed = wb.jobs.load_job(wb.jobs.root / f'{job.job_id}.failed.json')
    assert 'revision conflict' in failed.result['error']
    assert wb.uploads.get_by_path(segment['final_path'])['stage'] == 'staged'
    assert not wb.load(segment['segment_id'])['publish_approval']


def test_filtered_claim_does_not_preflight_or_consume_other_lane(tmp_path):
    jobs = ActionJobStore(tmp_path / 'jobs')
    heavy = jobs.submit('start_analysis', 'source')
    approval = jobs.submit('approve_publish', 'segment')
    seen = []
    def preflight(job):
        seen.append(job.action)
        return True, ''
    claimed = jobs.claim('approvals', preflight, action_filter=lambda a: a == 'approve_publish')
    assert claimed.job_id == approval.job_id and seen == ['approve_publish']
    assert jobs.valid_jobs(ActionJobStatus.PENDING)[0][1].job_id == heavy.job_id


def test_stop_finishes_inflight_approval_before_releasing_owner(workbench, tmp_path, monkeypatch):
    wb = workbench
    segment = staged_segment(wb)
    job = wb.submit('approve_publish', segment['segment_id'], 1)
    entered, release = threading.Event(), threading.Event()
    activate = wb.uploads.activate
    def blocking_activate(path):
        entered.set()
        assert release.wait(10)
        return activate(path)
    monkeypatch.setattr(wb.uploads, 'activate', blocking_activate)
    worker = Worker(wb.jobs, None, tmp_path / 'worker.lock', workbench=wb)
    errors = []
    def run():
        try:
            worker.run_forever(idle_timeout=30)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        worker.stop()
        assert thread.is_alive() and wb.jobs.owner_alive(worker.worker_id)
        assert wb.jobs.requeue_orphans(time.time() + 99999, stale_seconds=0) == 0
    finally:
        worker.stop()
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert (wb.jobs.root / f"{job['job_id']}.done.json").exists()
    assert wb.uploads.get_by_path(segment['final_path'])['stage'] == 'queued'
    assert not wb.jobs.owner_alive(worker.worker_id)
