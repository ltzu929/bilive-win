import json
from pathlib import Path

import pytest

from bilive.db.conn import init_upload_db
from bilive.domain.models import ActionJob, ActionJobStatus, task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionConflict, ActionJobStore
from bilive.lifecycle.review import SourceReview
from bilive.lifecycle.retention import DAY
from bilive.media.workbench import Workbench
from bilive.media.subtitle_store import SubtitleStore
from bilive.upload.queue import UploadQueue


@pytest.fixture
def env(tmp_path):
    videos = tmp_path / 'Videos'
    videos.mkdir()
    (videos / 'session.mp4').write_bytes(b'isolated source')
    (videos / 'session.xml').write_text('<i/>')
    db = tmp_path / 'queue.db'
    init_upload_db(db)
    wb = Workbench(videos, tmp_path / 'artifacts', ActionJobStore(tmp_path / 'jobs'),
                   SubtitleStore(tmp_path / 'artifacts/subtitles'), UploadQueue(db))
    recycled = tmp_path / 'recycled'
    recycled.mkdir()
    def mover(paths):
        for path in paths:
            path.rename(recycled / path.name)
    return wb, SourceReview(wb, mover), task_id_from_source_relpath('session.mp4')


def test_no_content_requires_confirmation_and_recycles_complete_package(env):
    wb, service, task = env
    with pytest.raises(ActionConflict, match='confirmation'):
        service.finish(task)
    result = service.finish(task, no_content=True)
    assert result['review_saved'] and result['recycle_queued']
    assert (wb.videos / 'session.mp4.done').is_file()
    queued = wb.jobs.claim('test')
    service.execute(queued)
    record = service.load(task)
    assert record['review_state'] == 'trash_pending' and record['trash_status'] == 'done'
    assert len(record['trash_results']) == 3
    assert not (wb.videos / 'session.mp4').exists()


def test_enqueue_failure_does_not_undo_review(env, monkeypatch):
    wb, service, task = env
    def fail(*args, **kwargs):
        raise OSError('isolated queue failure')
    monkeypatch.setattr(wb.jobs, 'submit', fail)
    result = service.finish(task, True)
    assert result['review_saved'] and not result['recycle_queued']
    assert service.load(task)['review_state'] == 'review_complete'


def test_published_cleaned_final_does_not_block_review(env):
    wb, service, task = env
    s = wb.points('session.mp4', 0, 1, 'title', 'reason')
    final = wb.artifacts / 'final_output/final.mp4'
    wb.uploads.stage(str(final), title='title')
    wb.uploads.mark_published(str(final), 'BVconfirmed')
    s.update(final_path=str(final), final_revision=1, publish_approval=True)
    wb.save(s)
    assert service.finish(task)['review_saved']


def test_partial_move_resumes_frozen_manifest_even_source_is_gone(env):
    wb, service, task = env
    service.finish(task, True)
    queued = wb.jobs.claim('test')
    real = service.mover
    count = 0
    def fail_second(paths):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('isolated partial failure')
        real(paths)
    service.mover = fail_second
    with pytest.raises(OSError):
        service.execute(queued)
    assert len(service.load(task)['trash_results']) == 1
    assert not (wb.videos / 'session.mp4').exists()
    # Retry must use the existing lifecycle even though source resolution now fails.
    wb.jobs.fail(queued, {'error':'partial'})
    assert service.queue(task)['queued']
    service.mover = real
    service.execute(wb.jobs.claim('test'))
    assert service.load(task)['trash_status'] == 'done'


def test_unknown_missing_item_stops_instead_of_guessing_success(env):
    wb, service, task = env
    service.finish(task, True)
    queued = wb.jobs.claim('test')
    record = service.load(task)
    record['frozen_manifest'] = service.manifest('session.mp4', task)
    service.save(record)
    (wb.videos / 'session.mp4').unlink()
    with pytest.raises(FileNotFoundError, match='unknown'):
        service.execute(queued)
    assert service.load(task)['trash_status'] == 'failed'
    assert (wb.videos / 'session.xml').exists()


def test_protected_final_and_traversal_are_blocked_before_any_move(env):
    wb, service, task = env
    service.finish(task, True)
    queued = wb.jobs.claim('test')
    record = service.load(task)
    record['frozen_manifest'] = [dict(root='videos', relpath='../outside.mp4')]
    service.save(record)
    with pytest.raises(ValueError, match='escapes'):
        service.execute(queued)
    assert (wb.videos / 'session.mp4').exists()
    s = dict(segment_id='protected-test', source_relpath='session.mp4', revision=1)
    s['final_path'] = str(wb.videos / 'session.xml')
    wb.save(s)
    record['frozen_manifest'] = [dict(root='videos', relpath='session.xml')]
    service.save(record)
    with pytest.raises(ValueError, match='protected'):
        service.checked(record['frozen_manifest'][0], service.protected())


def test_expiry_queues_despite_unfinished_analysis_and_preserves_finals(env):
    wb, service, task = env
    service.ensure('session.mp4', now=100)
    wb.jobs.submit('start_analysis', task, {})
    result = service.maintenance(now=100 + 7 * DAY)[0]
    assert result['queued']
    final = wb.artifacts / 'final_output/keep.mp4'
    final.parent.mkdir(parents=True)
    final.write_bytes(b'protected final')
    queued = next(j for _, j in wb.jobs.valid_jobs(ActionJobStatus.PENDING) if j.action == 'force_expired')
    assert service.execute(queued)['ok']
    assert final.read_bytes() == b'protected final'


def test_source_raw_recycled_but_final_and_review_artifacts_survive(env):
    wb, service, task = env
    service.ensure('session.mp4', now=100)
    segment = wb.points('session.mp4', 0, 1, 'title', 'reason')
    folder = wb.artifacts / segment['segment_id']
    folder.mkdir(parents=True)
    for name in ('raw_candidate.mp4', 'raw_candidate.range.json', 'final_output.mp4',
                 'analysis_sidecar.json', 'finalize_meta.json', 'subtitle.ass'):
        (folder / name).write_bytes(b'isolated')
    segment['final_path'] = str(folder / 'final_output.mp4')
    wb.save(segment)
    service.queue(task, forced=True, now=100 + 7 * DAY)
    service.execute(wb.jobs.claim('test'))
    assert not (folder / 'raw_candidate.mp4').exists()
    assert not (folder / 'raw_candidate.range.json').exists()
    for name in ('final_output.mp4', 'analysis_sidecar.json', 'finalize_meta.json', 'subtitle.ass'):
        assert (folder / name).is_file()


def test_retention_dates_persist_and_maintenance_only_queues(env):
    wb, service, task = env
    record = service.ensure('session.mp4', now=100)
    assert service.ensure('session.mp4', now=10000)['deadline'] == record['deadline']
    at5 = service.maintenance(now=100 + 5 * DAY)[0]
    assert at5['warn_day5'] and not at5['expired_day7'] and not wb.jobs.busy()
    at7 = service.maintenance(now=100 + 7 * DAY)[0]
    assert at7['queued']
    assert (wb.videos / 'session.mp4').exists()
    queued = wb.jobs.claim('test')
    assert queued.action == 'force_expired'
    service.execute(queued)
    assert service.load(task)['trash_status'] == 'done'


def test_legacy_recording_date_precedes_creation_time_and_invalid_date_falls_back(env):
    import datetime
    wb, service, task = env
    valid = '123_20260923-21-10-00_(1).mp4'
    invalid = '123_20261340-21-10-00.mp4'
    for name in (valid, invalid):
        (wb.videos / name).write_bytes(b'isolated')
    now = 1800000000
    expected = datetime.datetime(2026, 9, 23, 21, 10, tzinfo=datetime.timezone.utc).timestamp()
    assert service.ensure(valid, now)['retention_started_at'] == expected
    assert service.ensure(invalid, now)['retention_started_at'] == now
