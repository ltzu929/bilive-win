"""Documented settings, publication and deletion boundaries on isolated data."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from bilive.config.settings import Settings, load_settings
from bilive.config.web_settings import save_changes
from bilive.domain.models import ActionJobStatus, task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionConflict
from bilive.lifecycle.retention import DAY, _parse_time_from_name
from bilive.vendor.streamer_profiles import patch_streamer_profile
from test_source_review import env


@pytest.fixture
def api_env(env, tmp_path, monkeypatch):
    wb, life, task = env
    config = tmp_path / 'settings.toml'
    config.write_text('[analysis]\nauto_start = true\n[retention]\nenabled = true\n', encoding='utf-8')
    settings = load_settings(config)
    settings.paths.videos_dir = wb.videos
    settings.paths.db_path = wb.uploads.db_path
    wb.settings_provider = lambda: load_settings(config)
    api = importlib.import_module('bilive.api.app')
    for name, value in {'settings':settings, 'workbench':wb, 'jobs':wb.jobs,
                        'upload_queue':wb.uploads, 'subtitles':wb.subtitles}.items():
        monkeypatch.setattr(api, name, value)
    monkeypatch.setattr(api, 'worker_control', SimpleNamespace(wake=lambda: {'state':'isolated'}))
    return TestClient(api.app), wb, life, task, config


def test_atomic_settings_preserve_comments_legacy_and_unknown_keys(tmp_path):
    path = tmp_path / 'settings.toml'
    path.write_text('# keep header\n[slice.mimo]\nmodel="legacy"\ntimeout=77\n'
                    '[publication]\n# keep comment\ndescription="""multi\nline"""\n'
                    '# following comment\ncustom="preserved"\n', encoding='utf-8')
    result = save_changes(path, {'mimo':{'timeout':88}, 'publication':{'description':'新的简介', 'tid':19}})
    saved = path.read_text(encoding='utf-8')
    assert '# keep header' in saved and '# following comment' in saved and 'custom="preserved"' in saved
    assert load_settings(path).mimo.model == 'legacy' and load_settings(path).mimo.timeout == 88
    assert result['restart_required'] == ['mimo.timeout']
    save_changes(path, {'publication':{'tid':None}})
    assert load_settings(path).publication.tid is None


def test_web_edits_override_legacy_aliases_after_reload(tmp_path):
    path = tmp_path / 'settings.toml'
    path.write_text('[slice.multi_modal]\nwhisper_batch_size=4\n'
                    '[slice.analysis]\ntrim_asr_padding_seconds=8\npre_judge_asr_max_chars=1234\n', encoding='utf-8')
    save_changes(path, {'subtitle':{'asr_batch':6, 'padding':7}, 'analysis':{'asr_prompt_max_chars':5000}})
    loaded = load_settings(path)
    assert (loaded.subtitle.asr_batch, loaded.subtitle.padding, loaded.analysis.asr_prompt_max_chars) == (6,7,5000)


@pytest.mark.parametrize('changes', [
    {'mimo':{'request_concurrency':True}}, {'mimo':{'timeout':0}}, {'mimo':{'base_url':'file:///secret'}},
    {'quality':{'quality':1.01}}, {'paths':{'cookie_file':'private'}}, {'retention':{'enabled':'true'}},
    {'publication':{'title_template':'{title.__class__}'}}, {'publication':{'tags':['x'*21]}},
    {'edit':{'font_size':'48'}}, {'edit':{'outline':True}},
])
def test_invalid_settings_leave_file_untouched(api_env, changes):
    client, _, _, _, config = api_env
    before = config.read_bytes()
    response = client.post('/api/config/settings', json={'changes':changes})
    assert response.status_code == 422, response.text
    assert config.read_bytes() == before


def test_unified_style_save_clears_omitted_optional_style(api_env):
    client, _, _, _, config = api_env
    save_changes(config, {'edit':{'alignment':8, 'outline':3, 'primary_colour':'&H00000000'}})
    response = client.post('/api/config/settings', json={'changes':{}, 'style':{'font_name':'Noto Sans SC', 'font_size':48}})
    assert response.status_code == 200, response.text
    style = load_settings(config).edit
    assert style.font_size == 48 and style.alignment is None and style.outline is None
    assert style.primary_colour is None


def test_unified_save_cancels_automatic_jobs_and_reports_env_override(api_env, monkeypatch):
    client, wb, _, task, config = api_env
    job = wb.jobs.submit('start_analysis', task, {'automatic':True})
    monkeypatch.setenv('BILIVE_WORKER_IDLE_TIMEOUT', '777')
    response = client.post('/api/config/settings', json={'changes':{
        'analysis':{'auto_start':False}, 'worker':{'idle_timeout':123},
        'publication':{'title_template':'{streamer} · {title}', 'tid':19},
        'retention':{'unprocessed_days':6}}, 'style':{'font_name':'Noto Sans SC', 'font_size':48, 'margin_v':110}})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['cancelled_pending'] == 1 and data['environment_overrides'] == ['worker.idle_timeout']
    assert data['restart_required'] == ['worker.idle_timeout']
    assert load_settings(config).subtitle_style()['font_size'] == 48
    assert not list(wb.jobs.valid_jobs(ActionJobStatus.PENDING))
    assert client.post('/api/config/settings', headers={'Origin':'http://evil.example'},
                       json={'changes':{'retention':{'enabled':False}}}).status_code == 403


def test_reading_lifecycle_projects_new_sources_without_writes(api_env):
    client, wb, _, task, config = api_env
    db_before = wb.uploads.db_path.read_bytes()
    first = client.get('/api/lifecycle').json()
    assert first['total'] == 1 and first['items'][0]['task_id'] == task
    assert first['items'][0]['deadline'] - first['items'][0]['retention_started_at'] == 5 * DAY
    assert not (wb.artifacts / 'lifecycle').exists()
    assert wb.uploads.db_path.read_bytes() == db_before
    assert client.get(f'/api/recordings/{task}/lifecycle').status_code == 200


def test_hold_cancels_pending_delete_and_claimed_delete_rechecks(env):
    wb, service, task = env
    service.ensure('session.mp4', now=100)
    service.queue(task, forced=True, now=100 + 5 * DAY)
    assert service.retain(task, True)['manual_keep']
    assert not list(wb.jobs.valid_jobs(ActionJobStatus.PENDING))
    assert not service.maintenance(now=100 + 6 * DAY)[0]['queued']
    service.retain(task, False)
    service.queue(task, forced=True, now=100 + 6 * DAY)
    claimed = wb.jobs.claim('test')
    service.retain(task, True)
    with pytest.raises(ActionConflict, match='manually retained'):
        service.execute(claimed)
    assert (wb.videos / 'session.mp4').is_file()


def test_policy_change_and_disabled_cleanup_rechecked_at_execution(api_env):
    client, wb, service, task, config = api_env
    service.ensure('session.mp4', now=100)
    service.queue(task, forced=True, now=100 + 5 * DAY)
    job = wb.jobs.claim('test')
    assert client.post('/api/config/settings', json={'changes':{'retention':{'enabled':False}}}).status_code == 200
    with pytest.raises(ActionConflict, match='disabled'):
        service.execute(job)
    assert (wb.videos / 'session.mp4').is_file()


@pytest.mark.parametrize('kind', ['analysis', 'finalize', 'remux', 'recording'])
def test_expiry_never_moves_active_source(env, kind):
    wb, service, task = env
    service.ensure('session.mp4', now=100)
    segment = wb.points('session.mp4', 0, 1, 'title', 'reason')
    if kind == 'recording':
        wb.recording_active_provider = lambda rel:True
    else:
        target = segment['segment_id'] if kind == 'finalize' else task_id_from_source_relpath('session.flv') if kind == 'remux' else task
        wb.jobs.submit('finalize' if kind == 'finalize' else 'remux_recording' if kind == 'remux' else 'start_analysis', target, {})
    result = service.maintenance(now=100 + 5 * DAY)[0]
    assert not result['queued'] and (wb.videos / 'session.mp4').is_file()


def test_all_retained_publications_use_last_success_plus_24_hours(env, monkeypatch):
    wb, service, task = env
    service.ensure('session.mp4', now=100)
    segments = [wb.points('session.mp4', start, start+1, 'title', 'reason') for start in (0, 2)]
    for index, s in enumerate(segments):
        path = str(wb.artifacts / s['segment_id'] / 'final_output.mp4')
        wb.uploads.stage(path, title='title')
        monkeypatch.setattr('bilive.upload.queue.time.time', lambda:1000000 + index * 100)
        wb.uploads.mark_published(path, 'BVtest')
        s.update(final_path=path, final_revision=s['revision'], publish_approval=True)
        wb.save(s)
    before = service.maintenance(now=1000100 + 24 * 3600 - 1)[0]
    assert not before['expired'] and before['cleanup_reason'] == 'published'
    assert before['deadline'] == 1000100 + 24 * 3600
    after = service.maintenance(now=before['deadline'])[0]
    assert after['queued']
    monkeypatch.setattr('bilive.upload.queue.time.time', lambda:2000000)
    wb.uploads.mark_published(segments[-1]['final_path'], 'BVtest')
    assert wb.uploads.get_by_path(segments[-1]['final_path'])['published_at'] == 1000100


def test_global_streamer_and_segment_defaults_are_independent(api_env):
    client, wb, _, _, config = api_env
    folder = wb.videos / '123 - 测试主播'
    folder.mkdir()
    rel = '123 - 测试主播/blive_123_2026-10-05-120000.mp4'
    (wb.videos / rel).write_bytes(b'isolated')
    save_changes(config, {'publication':{'title_template':'{streamer}：{title}', 'description':'全局简介', 'tags':['全局'], 'tid':19}})
    patch_streamer_profile(wb.videos, '123', {'display_name':'主播', 'default_title_template':'{date} {title}', 'default_tid':138})
    s = wb.points(rel, 0, 1, '话题', 'reason')
    assert s['title'] == '2026-10-05 话题' and s['description'] == '全局简介' and s['tid'] == 138
    updated = client.post(f"/api/segments/{s['segment_id']}/metadata", json={
        'expected_revision':s['revision'], 'title':'临时', 'description':'临时简介', 'tags':['临时'], 'tid':17}).json()
    assert updated['tid'] == 17
    profile = client.get('/api/streamers/123/profile').json()['profile']
    assert profile['default_tid'] == 138 and profile['default_description'] == ''


def test_compact_recording_filename_uses_beijing_time():
    from datetime import datetime, timedelta, timezone
    expected = datetime(2026,10,5,12,tzinfo=timezone(timedelta(hours=8))).timestamp()
    assert _parse_time_from_name('blive_123_2026-10-05-120000.mp4') == expected
