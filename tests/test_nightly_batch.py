"""Cutoff, remote creation and recovery through persisted recording/job state."""
import json
import os
from datetime import datetime
from types import SimpleNamespace

import pytest

from bilive.analysis import nightly
from bilive.analysis.tasks import RecordingTasks
from bilive.db.conn import init_upload_db
from bilive.domain.models import ActionJobStatus, task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionConflict, ActionJobStore
from bilive.lifecycle.review import SourceReview
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench
from bilive.upload.queue import UploadQueue


def stamp(day, hour, minute):
    return datetime(2026, 11, day, hour, minute, tzinfo=nightly.BEIJING).timestamp()


class Remote:
    def __init__(self):
        self.uploads = []
        self.created = []
        self.finished = False
        self.lost_response = False

    def upload(self, path):
        assert path.is_file()
        self.uploads.append(path)
        return 'file-test'

    def create(self, file_id, name):
        self.created.append(file_id)
        if self.lost_response:
            raise ConnectionError('response lost after acceptance')
        return 'batch-test'

    def find(self, file_id):
        return 'batch-test' if file_id in self.created else None

    def status(self, batch_id):
        assert batch_id == 'batch-test'
        return {'status': 'completed' if self.finished else 'in_progress'}

    def results(self, status, ids):
        return {cid: {'custom_id': cid, 'response': {'status_code': 200}} for cid in ids}


@pytest.fixture
def recording(tmp_path, monkeypatch):
    videos, artifacts = tmp_path / 'videos', tmp_path / 'artifacts'
    db = tmp_path / 'queue.db'
    init_upload_db(db)
    wb = Workbench(videos, artifacts, ActionJobStore(tmp_path / 'jobs'),
                   SubtitleStore(artifacts / 'subtitles'), UploadQueue(db))
    rel = '123 - Test/session.mp4'
    source = wb.videos / rel
    source.parent.mkdir(parents=True)
    source.write_bytes(b'verified by remux')
    os.utime(source, (stamp(22, 23, 28), stamp(22, 23, 28)))
    policy = dict(nightly_batch_enabled=True, nightly_batch_enabled_at=stamp(22, 20, 0))
    monkeypatch.setattr(nightly, 'profile_for_source', lambda *args: policy)
    applied = []

    def prepare(rel, task, folder):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'batch-input-test.jsonl').write_text('{"custom_id":"candidate-1"}\n', encoding='utf-8')
        nightly._write(folder / 'analysis-plan.json', {'parts': [
            {'file': 'batch-input-test.jsonl', 'custom_ids': ['candidate-1']}]})

    def apply(rel, task, folder, rows):
        applied.append(rows)
        nightly._write(source.with_suffix('.mp4.task.json'),
                      {'status': 'done', 'batch_stage': 'completed', 'candidates': [], 'segments': []})
        return {'ok': True, 'candidates': 1}

    return SimpleNamespace(wb=wb, rel=rel, task=task_id_from_source_relpath(rel), source=source,
                           policy=policy, applied=applied, pipeline=SimpleNamespace(
                               prepare_batch=prepare, apply_batch=apply), remote=Remote())


def advance(rec, now):
    return nightly.execute_batch(rec.wb, rec.pipeline, rec.task, clock=lambda: now,
                                 client_factory=lambda: rec.remote)


def test_verified_preparation_cutoff_and_collect_after_restart(recording):
    r = recording
    assert nightly.register_remux(r.wb, r.rel, now=stamp(22, 23, 29))
    assert advance(r, stamp(22, 23, 29))['phase'] == 'ready'
    assert not r.remote.created
    with pytest.raises(ActionConflict):
        RecordingTasks(r.wb).queue(r.task)
    with pytest.raises(ActionConflict):
        SourceReview(r.wb).finish(r.task, no_content=True)
    with pytest.raises(ActionConflict):
        SourceReview(r.wb).active(r.rel)

    wakes = []
    control = SimpleNamespace(wake=lambda: wakes.append(True))
    nightly.schedule_tick(r.wb, control, stamp(22, 23, 29))
    assert not wakes and not r.wb.jobs.busy()
    for _ in range(2):
        nightly.schedule_tick(r.wb, control, stamp(22, 23, 30))
    pending = r.wb.jobs.valid_jobs(ActionJobStatus.PENDING)
    assert len(pending) == 1 and pending[0][1].action == 'nightly_batch'
    r.wb.jobs.cancel_pending(pending[0][1].job_id)
    assert advance(r, stamp(22, 23, 30))['phase'] == 'submitted'
    assert not r.wb.jobs.busy()
    # A cloud wait protects the recording from recycling, but leaves manual
    # work on this source available until the local importer takes its turn.
    manual = r.wb.points(r.rel, 1, 4, '手选反应', '补选')
    assert manual['source_relpath'] == r.rel

    # The next worker only has the saved ledger and the provider's state.
    r.remote.finished = True
    assert advance(r, stamp(23, 9, 0))['ok']
    assert r.remote.created == ['file-test'] and len(r.applied) == 1
    assert r.source.with_suffix('.mp4.done').is_file()
    assert not list(nightly._folder(r.wb, r.task).glob('batch-input-*.jsonl'))
    assert advance(r, stamp(23, 23, 30))['already_finished']
    assert nightly.register_remux(r.wb, r.rel, now=stamp(23, 23, 30))
    nightly.check_batch_idle(r.wb, r.task)


def test_verified_after_cutoff_waits_for_following_night(recording):
    r = recording
    assert nightly.register_remux(r.wb, r.rel, now=stamp(22, 23, 31))
    for at in (stamp(22, 23, 31), stamp(23, 0, 0), stamp(23, 12, 0)):
        assert advance(r, at)['phase'] == 'ready'
    assert not r.remote.uploads
    assert advance(r, stamp(23, 23, 30))['phase'] == 'submitted'


def test_lost_create_response_is_found_without_second_charge(recording):
    r = recording
    nightly.register_remux(r.wb, r.rel, now=stamp(22, 23, 28))
    r.remote.lost_response = True
    assert advance(r, stamp(22, 23, 30))['phase'] == 'submitted'
    assert r.remote.created == ['file-test']
    r.remote.finished = True
    assert advance(r, stamp(23, 8, 30))['ok']
    assert len(r.remote.uploads) == len(r.remote.created) == 1


def test_shutdown_after_receipt_does_not_rerun_a_failed_batch(recording, monkeypatch):
    r = recording
    nightly.register_remux(r.wb, r.rel, now=stamp(22, 23, 28))
    r.policy['nightly_batch_enabled'] = False
    cleanup = nightly._release_inputs
    def power_off(folder, path):
        raise SystemExit
    monkeypatch.setattr(nightly, '_release_inputs', power_off)
    with pytest.raises(SystemExit):
        advance(r, stamp(22, 23, 30))
    monkeypatch.setattr(nightly, '_release_inputs', cleanup)
    assert advance(r, stamp(23, 8, 0)) == {'ok': False, 'already_finished': True}
    nightly.check_batch_idle(r.wb, r.task)
    assert not r.remote.created


def test_result_file_temporarily_locked_keeps_cloud_id(recording, monkeypatch):
    r = recording
    nightly.register_remux(r.wb, r.rel, now=stamp(22, 23, 28))
    advance(r, stamp(22, 23, 30))
    r.remote.finished = True
    read = nightly._read
    def locked(path):
        if path.name.startswith('result-'):
            raise PermissionError('sharing violation')
        return read(path)
    monkeypatch.setattr(nightly, '_read', locked)
    with pytest.raises(PermissionError):
        advance(r, stamp(23, 8, 30))
    monkeypatch.setattr(nightly, '_read', read)
    assert advance(r, stamp(23, 8, 31))['ok']
    assert len(r.remote.created) == len(r.applied) == 1


def test_disable_before_sending_and_post_enable_scope(recording):
    r = recording
    r.policy['nightly_batch_enabled'] = False
    assert not nightly.register_remux(r.wb, r.rel)
    r.policy.update(nightly_batch_enabled=True, nightly_batch_enabled_at=stamp(23, 0, 1))
    assert not nightly.register_remux(r.wb, r.rel, now=stamp(23, 12, 0))
    r.policy['nightly_batch_enabled_at'] = stamp(22, 20, 0)
    nightly.register_remux(r.wb, r.rel, now=stamp(22, 23, 28))
    advance(r, stamp(22, 23, 29))
    r.policy['nightly_batch_enabled'] = False
    assert advance(r, stamp(22, 23, 30))['cancelled']
    assert not r.remote.created
    assert json.loads(r.source.with_suffix('.mp4.task.json').read_bytes())['status'] == 'batch_failed'
    assert RecordingTasks(r.wb).queue(r.task).action == 'start_analysis'


def test_remux_worker_prefers_nightly_over_global_realtime(recording, monkeypatch):
    from bilive.worker.runner import Worker
    r = recording
    monkeypatch.setattr('bilive.media.remux.remux_recording', lambda _: SimpleNamespace(
        mp4_path=str(r.source), verified=True))
    monkeypatch.setattr(nightly.time, 'time', lambda: stamp(22, 23, 29))
    worker = Worker(r.wb.jobs, SimpleNamespace(videos_root=r.wb.videos), r.wb.artifacts / 'worker.lock',
                    workbench=r.wb, auto_analysis_enabled=lambda: True)
    action = SimpleNamespace(action='remux_recording', payload={}, target=task_id_from_source_relpath(
        r.source.with_suffix('.flv').relative_to(r.wb.videos).as_posix()))
    assert worker.execute(action)['analysis_mode'] == 'nightly_batch'
    assert not r.wb.jobs.busy() and nightly._pending(r.wb, r.task).exists()
    for _ in range(2):
        nightly.schedule_tick(r.wb, SimpleNamespace(wake=lambda: None), stamp(22, 23, 29))
    job, = [j for _, j in r.wb.jobs.valid_jobs(ActionJobStatus.PENDING)]
    assert job.target == r.task and job.action == 'nightly_batch'
