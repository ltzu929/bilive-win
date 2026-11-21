"""Interrupted/failed FLV recordings share normal source-package retention."""
import os

import pytest

from bilive.config.settings import RetentionConfig, Settings
from bilive.domain.models import ActionJobStatus, task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionConflict
from bilive.lifecycle.retention import DAY
from test_source_review import env


@pytest.fixture
def flv(env):
    wb, service, task = env
    (wb.videos / 'session.mp4').unlink()
    source = wb.videos / 'session.flv'
    source.write_bytes(b'interrupted recording')
    os.utime(source, (100, 100))
    settings = Settings(retention=RetentionConfig(
        enabled=True, unprocessed_days=5, published_hours=120))
    wb.settings_provider = lambda: settings
    return wb, service, task, source


def test_flv_only_expires_at_file_age_and_recycles_sidecars_once(flv):
    wb, service, task, source = flv
    meta = source.with_suffix('.flv.meta.json')
    meta.write_text('{}')
    unrelated = source.with_name('session_(1).xml')
    unrelated.write_text('different recording')
    assert not service.maintenance(now=100 + 5 * DAY - 1)[0]['expired']
    assert not wb.jobs.busy()
    row = service.maintenance(now=100 + 5 * DAY)[0]
    assert row['task_id'] == task and row['queued']
    assert service.view(task)['retention_started_at'] == 100
    job = wb.jobs.claim('test')
    result = service.execute(job)
    assert result == {'ok': True, 'trash_status': 'done', 'moved': 3}
    assert not source.exists() and not source.with_suffix('.xml').exists()
    assert not meta.exists() and unrelated.is_file()
    assert service.execute(job)['already_recycled']


def test_named_flv_uses_recording_date_instead_of_discovery_time(flv):
    from datetime import datetime, timedelta, timezone
    wb, service, _, source = flv
    source = source.rename(source.with_name('blive_123_2026-10-01-120000_(1).flv'))
    os.utime(source, (1800000000, 1800000000))
    started = datetime(2026, 10, 1, 12, tzinfo=timezone(timedelta(hours=8))).timestamp()
    row = service.maintenance(now=started + 5 * DAY)[0]
    assert row['queued'] and row['deadline'] == started + 5 * DAY
    assert service.load(row['task_id'])['retention_started_at'] == started


@pytest.mark.parametrize('protection', [
    'disabled', 'manual', 'analysis', 'remux_pending', 'remux_processing', 'recording',
])
def test_orphan_flv_retains_source_protections(flv, protection):
    wb, service, task, source = flv
    if protection == 'disabled':
        wb.settings_provider().retention.enabled = False
    elif protection == 'manual':
        service.retain(task, True)
    elif protection == 'analysis':
        wb.jobs.submit('start_analysis', task, {})
    elif protection.startswith('remux'):
        wb.jobs.submit('remux_recording', task_id_from_source_relpath('session.flv'), {})
        if protection == 'remux_processing':
            wb.jobs.claim('test')
    else:
        wb.recording_active_provider = lambda rel: True
    row = service.maintenance(now=100 + 5 * DAY)[0]
    assert not row.get('queued') and source.is_file()
    assert not any(j.action == 'force_expired' for _, j in wb.jobs.valid_jobs(ActionJobStatus.PENDING))


def test_mp4_and_flv_pair_share_one_job_and_publication_deadline(flv):
    wb, service, task, source = flv
    source.with_suffix('.mp4').write_bytes(b'completed recording')
    segment = wb.points('session.mp4', 0, 1, 'title', 'reason')
    final = wb.artifacts / segment['segment_id'] / 'final_output.mp4'
    final.parent.mkdir(parents=True)
    final.write_bytes(b'published output')
    segment['final_path'] = str(final)
    wb.save(segment)
    wb.uploads.stage(str(final), title='title')
    published_at = wb.uploads.mark_published(str(final), 'BV-test')['published_at']
    rows = service.maintenance(now=published_at + 5 * DAY - 1)
    assert len(rows) == 1 and not rows[0]['expired']
    rows = service.maintenance(now=published_at + 5 * DAY)
    assert len(rows) == 1 and rows[0]['queued']
    assert service.maintenance(now=published_at + 5 * DAY)[0]['job_id'] == rows[0]['job_id']
    manifest = service.manifest('session.mp4', task)
    assert len(manifest) == len({(e['root'], e['relpath']) for e in manifest}) == 3
    assert final.is_file()


def test_execute_rechecks_manual_hold_after_queueing(flv):
    wb, service, task, source = flv
    service.maintenance(now=100 + 5 * DAY)
    job = wb.jobs.claim('test')
    record = service.load(task)
    record['manual_keep'] = True
    service.save(record)
    with pytest.raises(ActionConflict, match='manually retained'):
        service.execute(job)
    assert source.is_file()


def test_failed_flv_job_does_not_prevent_expiry(flv):
    wb, service, _, source = flv
    wb.jobs.submit('remux_recording', task_id_from_source_relpath('session.flv'), {})
    job = wb.jobs.claim('test')
    wb.jobs.fail(job, {'error': 'missing audio/video stream'})
    assert service.maintenance(now=100 + 5 * DAY)[0]['queued']
    assert service.execute(wb.jobs.claim('test'))['ok']
    assert not source.exists()


def test_partial_flv_recycle_resumes_after_flv_is_gone(flv):
    wb, service, task, source = flv
    service.maintenance(now=100 + 5 * DAY)
    job = wb.jobs.claim('test')
    real = service.mover
    def fail_xml(paths):
        if paths[0].suffix == '.xml':
            raise OSError('isolated recycle failure')
        real(paths)
    service.mover = fail_xml
    with pytest.raises(OSError, match='isolated'):
        service.execute(job)
    assert not source.exists() and source.with_suffix('.xml').exists()
    wb.jobs.fail(job, {'error': 'partial recycle'})
    service.queue(task, forced=True, now=100 + 5 * DAY)
    service.mover = real
    assert service.execute(wb.jobs.claim('test'))['ok']
    assert service.load(task)['trash_status'] == 'done'


def test_flv_alias_outside_videos_is_not_scanned(flv, tmp_path):
    wb, service, _, source = flv
    outside = tmp_path / 'outside.flv'
    source.rename(outside)
    try:
        source.symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable')
    assert service.maintenance(now=100 + 30 * DAY) == []
    assert outside.is_file()


def test_upload_reference_protects_orphan_flv_before_any_move(flv):
    wb, service, _, source = flv
    wb.uploads.stage(str(source), title='protected media')
    assert service.maintenance(now=100 + 5 * DAY) == []
    assert not wb.jobs.busy()
    assert source.is_file() and source.with_suffix('.xml').is_file()


@pytest.mark.parametrize('recorder_state', ['recording', 'unavailable'])
def test_live_recorder_check_matches_flv_path_using_mp4_identity(flv, recorder_state):
    from bilive.lifecycle.runtime import configure_workbench
    from bilive.recorder.client import RecorderUnavailable
    wb, service, _, source = flv
    source = source.rename(source.with_name('blive_123_2020-01-01-120000.flv'))
    settings = wb.settings_provider()
    class Recorder:
        def request(self, path):
            assert path == '/123/data'
            if recorder_state == 'unavailable':
                raise RecorderUnavailable('isolated unavailable recorder')
            return {'task_status': {'running_status': 'recording', 'recording_path': str(source)}}
    configure_workbench(wb, None, recorder=Recorder())
    wb.settings_provider = lambda: settings
    row = service.maintenance()[0]
    assert row['expired'] and not row['queued']
    assert 'recording is active' in row['reason'] and source.is_file()
