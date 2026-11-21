"""Clip expiry boundaries and protection of unfinished work, on isolated files."""
import os
from pathlib import Path

import pytest

from bilive.config.settings import RetentionConfig, Settings
from bilive.lifecycle.clip_retention import cleanup_clips
from bilive.lifecycle.retention import DAY
from test_source_review import env


@pytest.fixture
def clip(env):
    wb, lifecycle, task = env
    settings = Settings(retention=RetentionConfig(
        enabled=True, unprocessed_days=5, published_hours=120))
    wb.settings_provider = lambda: settings
    segment = wb.points('session.mp4', 0, 1, '人工选择的片段', 'fixture')
    folder = wb.artifacts / segment['segment_id']
    folder.mkdir(parents=True)
    path = folder / 'final_output.mp4'
    path.write_bytes(b'isolated clip output')
    segment['final_path'] = str(path)
    wb.save(segment)
    wb.uploads.stage(str(path), title=segment['title'])
    return wb, lifecycle, task, segment, path


def publish(wb, path):
    return wb.uploads.mark_published(str(path), 'BV-test')['published_at']


def test_published_clip_kept_for_five_days_and_evidence_survives(clip):
    wb, _, _, segment, path = clip
    stamp = publish(wb, path)
    metadata = wb.root / f"{segment['segment_id']}.json"
    before = metadata.read_bytes()
    subtitle = path.with_name('subtitle.ass')
    subtitle.write_text('saved subtitle evidence', encoding='utf-8')
    assert cleanup_clips(wb, stamp + 5 * DAY - .001) == []
    assert path.exists()
    assert cleanup_clips(wb, stamp + 5 * DAY)[0]['deleted']
    assert not path.exists() and subtitle.is_file()
    assert metadata.read_bytes() == before
    assert wb.uploads.get_by_path(str(path))['stage'] == 'published'
    assert cleanup_clips(wb, stamp + 6 * DAY) == []


def test_unpublished_output_uses_creation_window_and_replacement_resets_it(clip):
    wb, _, _, _, path = clip
    stamp = 1000000
    os.utime(path, (stamp, stamp))
    assert cleanup_clips(wb, stamp + 5 * DAY - 1) == []
    os.utime(path, (stamp + DAY, stamp + DAY))
    assert cleanup_clips(wb, stamp + 5 * DAY) == []
    assert cleanup_clips(wb, stamp + 6 * DAY)[0]['deleted']


@pytest.mark.parametrize('stage', ['queued', 'uploaded', 'uploading', 'publishing'])
def test_upload_in_progress_is_never_expired(clip, stage):
    wb, _, _, _, path = clip
    os.utime(path, (100, 100))
    conn = wb.uploads._conn()
    try:
        conn.execute('UPDATE upload_queue SET stage=? WHERE video_path=?', (stage, str(path)))
        conn.commit()
    finally:
        conn.close()
    assert cleanup_clips(wb, 100 + 30 * DAY) == []
    assert path.exists()


@pytest.mark.parametrize('protection', ['disabled', 'manual', 'pending', 'processing'])
def test_disabled_manual_and_active_source_protections(clip, protection):
    wb, lifecycle, task, _, path = clip
    stamp = publish(wb, path)
    if protection == 'disabled':
        wb.settings_provider().retention.enabled = False
    elif protection == 'manual':
        record = lifecycle.ensure('session.mp4')
        record['manual_keep'] = True
        lifecycle.save(record)
    else:
        wb.jobs.submit('start_analysis', task, {})
        if protection == 'processing':
            wb.jobs.claim('isolated-test')
    assert cleanup_clips(wb, stamp + 30 * DAY) == []
    assert path.exists()


def test_path_outside_segment_folder_is_not_deleted(clip, tmp_path):
    wb, _, _, segment, path = clip
    outside = tmp_path / 'outside' / 'final_output.mp4'
    outside.parent.mkdir()
    outside.write_bytes(b'not owned by this segment')
    wb.uploads.stage(str(outside), title='outside')
    stamp = publish(wb, outside)
    segment['final_path'] = str(outside)
    wb.save(segment)
    assert cleanup_clips(wb, stamp + 30 * DAY) == []
    assert outside.exists() and path.exists()


@pytest.mark.parametrize('field,value', [('bvid', None), ('published_at', 'unknown')])
def test_unverified_publication_or_timestamp_preserves_output(clip, field, value):
    wb, _, _, _, path = clip
    stamp = publish(wb, path)
    conn = wb.uploads._conn()
    try:
        conn.execute(f'UPDATE upload_queue SET {field}=? WHERE video_path=?', (value, str(path)))
        conn.commit()
    finally:
        conn.close()
    cleanup_clips(wb, stamp + 30 * DAY)
    assert path.exists()


def test_path_alias_with_active_queue_row_preserves_output(clip):
    wb, _, _, _, path = clip
    stamp = publish(wb, path)
    alias = os.path.relpath(path, Path.cwd())
    wb.uploads.stage(alias, title='active alias')
    wb.uploads.activate(alias)
    assert cleanup_clips(wb, stamp + 30 * DAY) == []
    assert path.exists()
