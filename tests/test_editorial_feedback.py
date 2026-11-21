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
    # This fixture borrows point creation for a synthetic model result, not a human selection.
    from bilive.vendor.streamer_profiles import _experience_path
    _experience_path(wb.videos).unlink()
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
    ('publication', False, 'publication_preference'), ('boundary', False, 'boundary_correction'),
    ('understanding', False, 'understanding_correction'), ('mixed', False, 'mixed_feedback'),
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
    evidence = streamer_evidence_summary(wb.videos, '123')
    assert evidence['negative_count'] == (expected == 'negative')
    assert evidence['sample_size'] == (expected in {'negative', 'publication_preference', 'boundary_correction', 'understanding_correction'})


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


def test_feedback_is_collected_immediately_and_finish_does_not_duplicate(editorial_env):
    wb, client = editorial_env
    s = automatic(wb)
    dropped = post(client, s, 'drop', reason='有意思，但不想发布', kind='publication')
    rows = read_experiences(wb.videos)
    assert len(rows) == 1
    assert rows[0]['experience_type'] == 'publication_preference'
    assert rows[0]['model_judgment'] == 'keep'
    assert rows[0]['human_reason'] == '有意思，但不想发布'
    assert SourceReview(wb).load(task_id_from_source_relpath(REL)) is None
    SourceReview(wb).finish(task_id_from_source_relpath(REL), no_content=True)
    assert len(read_experiences(wb.videos)) == 1
    assert dropped['state'] == 'dropped'


def test_edit_versions_count_once_and_never_turn_a_trim_into_a_quality_negative(editorial_env):
    wb, client = editorial_env
    s = automatic(wb)
    s = post(client, s, 'keep', reason='短梗可留')
    for end in (34, 33):
        s = wb.points(REL, 12, end, s['title'], '保留最后回应', s['segment_id'], s['revision'])
    summary = streamer_evidence_summary(wb.videos, '123')
    assert summary['sample_size'] == summary['correction_count'] == 1
    assert summary['positive_count'] == summary['negative_count'] == 0
    assert len(read_experiences(wb.videos)) == 3
    kept = post(client, s, 'keep')
    summary = streamer_evidence_summary(wb.videos, '123')
    assert summary['sample_size'] == summary['positive_count'] == 1
    assert summary['correction_count'] == 0
    assert kept['source_end'] == 33


def test_backfill_after_source_cleanup_preserves_classification_and_is_idempotent(editorial_env):
    from bilive.lifecycle.profiles import sync_review_feedback
    from bilive.vendor.streamer_profiles import _experience_path
    wb, client = editorial_env
    s = automatic(wb)
    dropped = post(client, s, 'drop', reason='有趣但不想发布这种争议内容')
    _experience_path(wb.videos).unlink()  # Simulate a historical revision collected before this change.
    wb.source(REL).unlink()
    before = (wb.root / f"{s['segment_id']}.json").read_bytes()
    classification = {s['segment_id']: {'kind': 'publication', 'basis': '用户明确确认有趣但不想发布'}}
    preview = sync_review_feedback(wb, classifications=classification)
    assert preview['segments'] == 1 and not _experience_path(wb.videos).exists()
    for _ in range(2):
        sync_review_feedback(wb, classifications=classification, write=True)
    # Finishing the same saved revision cannot overwrite an explicit reviewed classification.
    record_review(wb, REL, 'task', [dropped], '')
    rows = read_experiences(wb.videos)
    assert len(rows) == 1 and rows[0]['experience_type'] == 'publication_preference'
    assert rows[0]['classification'] == classification[s['segment_id']]
    assert (wb.root / f"{s['segment_id']}.json").read_bytes() == before


def test_feedback_category_survives_optional_keep_and_invalid_kind_changes_nothing(editorial_env):
    wb, client = editorial_env
    s = automatic(wb)
    s = post(client, s, 'feedback', reason='读的是观众留言', kind='understanding')
    s = post(client, s, 'keep')
    assert s['human_feedback']['kind'] == 'understanding'
    count = len(read_experiences(wb.videos))
    assert client.post(f"/api/segments/{s['segment_id']}/feedback", json={
        'expected_revision': s['revision'], 'reason': 'bad', 'kind': 'invented',
    }).status_code == 422
    assert len(read_experiences(wb.videos)) == count


def test_generate_endpoint_backfills_human_history_and_never_applies_guidance(editorial_env):
    from bilive.vendor.streamer_profiles import _experience_path, read_streamer_profile
    wb, client = editorial_env
    automatic(wb)
    assert not _experience_path(wb.videos).exists()
    # Unreviewed model keep is not backfilled as human approval.
    response = client.post('/api/streamers/123/recommendations/generate')
    assert response.status_code == 200
    assert not read_experiences(wb.videos)
    for i in range(5):
        s = wb.points(REL, 40+i*3, 42+i*3, f'人工补选{i}', '')
        if i == 4:
            wb.drop(s['segment_id'], s['revision'], '有趣但不想发', kind='publication')
    _experience_path(wb.videos).unlink()
    response = client.post('/api/streamers/123/recommendations/generate')
    assert response.status_code == 200
    data = response.json()
    assert data['evidence']['sample_size'] == 5
    assert data['evidence']['positive_count'] == 4
    assert data['evidence']['negative_count'] == 0
    assert data['recommendations'][-1]['status'] == 'pending'
    assert read_streamer_profile(wb.videos, '123')['approved_guidance'] == ''
    count = len(read_experiences(wb.videos))
    assert client.post('/api/streamers/123/recommendations/generate').status_code == 200
    assert len(read_experiences(wb.videos)) == count


def test_curated_lesson_is_separate_from_human_quote_and_survives_plain_backfill(editorial_env):
    from bilive.lifecycle.profiles import record_segment_review, sync_review_feedback
    from bilive.vendor.streamer_profiles import latest_experiences
    wb, client = editorial_env
    s = post(client, automatic(wb), 'keep')
    record_segment_review(wb, s, classification={'kind': 'content', 'review_note': '真正主线是观众选了笑话而非八卦', 'basis': '本聊天用户已确认'})
    sync_review_feedback(wb, write=True)
    latest = latest_experiences(read_experiences(wb.videos))[0]
    assert latest['human_reason'] == latest['note'] == ''
    assert latest['review_note'] == '真正主线是观众选了笑话而非八卦'
    assert latest['conclusion'] == 'positive'
    before = len(read_experiences(wb.videos))
    record_segment_review(wb, s, classification={'kind': 'content', 'review_note': latest['review_note'], 'basis': '本聊天用户已确认'})
    assert len(read_experiences(wb.videos)) == before
