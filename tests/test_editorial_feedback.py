import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bilive.config.settings import Settings
from bilive.db.conn import init_upload_db
from bilive.domain.models import SubtitleLine, task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionJobStore
from bilive.lifecycle.profiles import record_review
from bilive.lifecycle.review import SourceReview
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench
from bilive.upload.queue import UploadQueue
from bilive.vendor.streamer_profiles import read_experiences, streamer_evidence_summary


REL = '123 - 主播/session.mp4'


@pytest.fixture
def editorial_env(tmp_path, monkeypatch):
    videos = tmp_path / 'Videos'
    source = videos / REL
    source.parent.mkdir(parents=True)
    source.write_bytes(b'isolated fixture; no media processing')
    artifacts = tmp_path / 'artifacts'
    db = tmp_path / 'uploads.db'
    init_upload_db(db)
    wb = Workbench(videos, artifacts, ActionJobStore(tmp_path / 'jobs'),
                   SubtitleStore(artifacts / 'subtitles'), UploadQueue(db))
    api = importlib.import_module('bilive.api.app')
    settings = Settings()
    settings.paths.videos_dir = videos
    settings.paths.db_path = db
    for name, value in {'workbench': wb, 'jobs': wb.jobs, 'subtitles': wb.subtitles,
                        'upload_queue': wb.uploads, 'settings': settings}.items():
        monkeypatch.setattr(api, name, value)
    monkeypatch.setattr(api, 'worker_control', SimpleNamespace(wake=lambda: pytest.fail('must not start a worker')))
    return wb, TestClient(api.app)


def automatic(wb):
    s = wb.points(REL, 10, 30, '模型原标题', '')
    for key in ('editorial_history', 'human_feedback', 'selection_origin'):
        s.pop(key)
    s.update(decision='keep', note='模型觉得完整有反差', quality_score=.9)
    wb.save(s)
    return s


def post(client, s, action, **data):
    response = client.post(f"/api/segments/{s['segment_id']}/{action}",
                           json={'expected_revision': s['revision'], **data})
    assert response.status_code == 200, response.text
    return response.json()


def test_keep_records_human_reason_and_model_baseline_without_starting_processing(editorial_env):
    wb, client = editorial_env
    s = automatic(wb)
    wb.subtitles.save(s['segment_id'], 20, [SubtitleLine(1, 0, 1, '字幕')], s['revision'])
    s['subtitles_revision'] = s['revision']
    wb.save(s)
    kept = post(client, s, 'keep', reason='笑点是她听懂后的停顿')
    saved = wb.load(s['segment_id'])
    assert saved['revision'] == saved['subtitles_revision'] == s['revision']
    assert saved['note'] == s['note']
    event = saved['editorial_history'][0]
    assert event['before']['decision'] == 'keep'
    assert event['before']['note'] == '模型觉得完整有反差'
    assert event['after']['decision'] == 'manual_keep'
    assert event['feedback']['reason'] == '笑点是她听懂后的停顿'
    assert event['actor'] == 'human' and event['created_at']
    post(client, kept, 'keep', reason='笑点是她听懂后的停顿')
    assert len(wb.load(s['segment_id'])['editorial_history']) == 1
    record = record_review(wb, REL, 'task', [saved], '')[0]
    assert record['experience_type'] == 'positive'  # Confirming an AI clip is not a missed clip.
    assert record['model_judgment'] == 'keep'
    assert record['human_reason'] == event['feedback']['reason']


def test_optional_reason_does_not_copy_model_explanation(editorial_env):
    wb, client = editorial_env
    s = automatic(wb)
    kept = post(client, s, 'keep')
    record = record_review(wb, REL, 'task', [kept], '')[0]
    assert kept['human_feedback']['reason'] == record['human_reason'] == record['note'] == ''
    assert record['original_note'] == '模型觉得完整有反差'


def test_range_and_metadata_history_survive_reload_with_each_intermediate_version(editorial_env):
    wb, client = editorial_env
    s = automatic(wb)
    for start, end, reason in [(12, 35, '后面还有一句回应'), (12, 33, '最后两秒只是谢礼')]:
        response = client.post('/api/segments/point', json={
            'segment_id': s['segment_id'], 'expected_revision': s['revision'],
            'source_relpath': REL, 'source_start': start, 'source_end': end,
            'title': s['title'], 'note': reason,
        })
        assert response.status_code == 200, response.text
        s = response.json()
    for title, description, tags, dynamic, reason in [
        ('差点忘了开播', '原简介', ['聊天'], '动态一', '去掉没有依据的两小时'),
        ('我怎么又差点忘了开播', '改后简介', ['聊天', '整活'], '动态二', '用她自己的话'),
    ]:
        s = post(client, s, 'metadata', title=title, description=description,
                 tags=tags, dynamic=dynamic, reason=reason)
    saved = wb.load(s['segment_id'])
    history = saved['editorial_history']
    assert [event['action'] for event in history] == ['range', 'range', 'metadata', 'metadata']
    assert [event['sequence'] for event in history] == [1, 2, 3, 4]
    assert history[0]['before']['source_start'] == 10
    assert history[0]['after']['source_end'] == history[1]['before']['source_end'] == 35
    assert history[1]['after']['source_end'] == 33
    assert history[2]['before']['title'] == '模型原标题'
    assert history[3]['before']['title'] == '差点忘了开播'
    assert history[3]['before']['description'] == '原简介'
    assert history[3]['after']['dynamic'] == '动态二'
    assert history[2]['after']['tags'] == ['聊天']
    assert history[3]['after']['tags'] == ['聊天', '整活']
    assert saved['note'] == '模型觉得完整有反差'
    assert 'human_feedback' not in saved  # A trim reason is not a content preference.
    assert client.get(f"/api/segments/{s['segment_id']}").json()['editorial_history'] == history


def test_create_and_feedback_are_optional_and_do_not_invalidate_media(editorial_env):
    wb, client = editorial_env
    response = client.post('/api/segments/point', json={
        'source_relpath': REL, 'source_start': 1, 'source_end': 8, 'title': '人工补片',
    })
    assert response.status_code == 200, response.text
    s = response.json()
    assert s['selection_origin'] == 'manual'
    assert s['editorial_history'][0]['action'] == 'create'
    assert s['editorial_history'][0]['before'] == {}
    saved = post(client, s, 'feedback', reason='可留可不留', uncertain=True)
    assert saved['revision'] == s['revision']
    assert saved['decision'] == s['decision']
    kept = post(client, saved, 'keep')
    assert kept['human_feedback'] == saved['human_feedback']
    record = record_review(wb, REL, 'task', [kept], '')[0]
    assert record['experience_type'] == 'uncertain'
    assert streamer_evidence_summary(wb.videos, '123')['sample_size'] == 0


@pytest.mark.parametrize('kind,uncertain,expected', [
    ('content', False, 'negative'), ('content', True, 'uncertain'),
    ('technical', False, 'technical_failure'), ('duplicate', False, 'duplicate'),
])
def test_finished_review_keeps_history_and_distinguishes_disposal_reasons(editorial_env, kind, uncertain, expected):
    wb, client = editorial_env
    s = automatic(wb)
    dropped = post(client, s, 'drop', reason='我的原话', kind=kind, uncertain=uncertain)
    service = SourceReview(wb)
    result = service.finish(task_id_from_source_relpath(REL), no_content=True)
    assert result['review_saved']
    record = read_experiences(wb.videos, room_id='123')[0]
    assert record['experience_type'] == expected
    assert record['human_reason'] == '我的原话'
    assert record['editorial_history'] == dropped['editorial_history']
    assert record['model_judgment'] == 'keep'
    assert streamer_evidence_summary(wb.videos, '123')['sample_size'] == (expected == 'negative')


def test_rejected_writes_cannot_append_history(editorial_env):
    wb, client = editorial_env
    s = automatic(wb)
    path = wb.root / f"{s['segment_id']}.json"
    before = path.read_bytes()
    assert client.post(f"/api/segments/{s['segment_id']}/feedback", json={
        'expected_revision': 99, 'reason': 'stale',
    }).status_code == 409
    assert client.post(f"/api/segments/{s['segment_id']}/drop", json={
        'expected_revision': s['revision'], 'kind': 'invalid',
    }).status_code == 422
    assert client.post(f"/api/segments/{s['segment_id']}/keep", json={
        'expected_revision': s['revision'], 'reason': '字' * 1001,
    }).status_code == 422
    assert path.read_bytes() == before
