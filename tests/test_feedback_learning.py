"""Human feedback refreshes candidate guidance without applying it."""
import pytest

from bilive.db.conn import init_upload_db
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench
from bilive.upload.queue import UploadQueue
from bilive.vendor import streamer_profiles as profiles


@pytest.fixture
def workbench(tmp_path):
    videos = tmp_path / 'Videos'
    source = videos / '123 - 主播/session.mp4'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'isolated fixture; no media processing')
    artifacts = tmp_path / 'artifacts'
    db = tmp_path / 'uploads.db'
    init_upload_db(db)
    return Workbench(videos, artifacts, ActionJobStore(tmp_path / 'jobs'),
                     SubtitleStore(artifacts / 'subtitles'), UploadQueue(db))


def test_saved_feedback_refreshes_pending_guidance_without_finishing_source(workbench):
    wb = workbench
    for i in range(4):
        wb.points('123 - 主播/session.mp4', i * 3, i * 3 + 2, f'保留{i}', '')
    s = wb.points('123 - 主播/session.mp4', 20, 30, '候选', '')
    assert not profiles.read_streamer_recommendations(wb.videos, '123')
    wb.drop(s['segment_id'], s['revision'], '没有具体观看价值')
    first = profiles.read_streamer_recommendations(wb.videos, '123')[-1]
    assert first['status'] == 'pending'
    assert first['sample_size'] == 5 and first['negative_count'] == 1
    assert '没有具体观看价值' in first['changes']['approved_guidance']
    assert profiles.read_streamer_profile(wb.videos, '123')['approved_guidance'] == ''
    assert not profiles.read_streamer_recommendations(wb.videos, '456')

    applied = profiles.apply_streamer_recommendation(wb.videos, '123', first['recommendation_id'])
    approved = applied['profile']['approved_guidance']
    new = wb.points('123 - 主播/session.mp4', 40, 50, '新保留样本', '')
    new = wb.keep(new['segment_id'], new['revision'], '最后一句回扣前面的梗')
    recommendations = profiles.read_streamer_recommendations(wb.videos, '123')
    pending = recommendations[-1]
    assert pending['status'] == 'pending' and pending['sample_size'] == 6
    assert '最后一句回扣前面的梗' in pending['changes']['approved_guidance']
    assert sum(r['status'] == 'pending' for r in recommendations) == 1
    assert recommendations[0]['status'] == 'applied'
    assert profiles.read_streamer_profile(wb.videos, '123')['approved_guidance'] == approved
    count = len(profiles.read_experiences(wb.videos))
    wb.keep(new['segment_id'], new['revision'], '最后一句回扣前面的梗')
    assert len(profiles.read_experiences(wb.videos)) == count
    assert profiles.read_streamer_recommendations(wb.videos, '123') == recommendations
    assert not list(wb.jobs.root.glob('*.json'))


def test_recent_examples_share_space_with_historical_calibration(tmp_path):
    for i, (title, kind, note) in enumerate([
        ('较早人工校准', 'positive', '较早的复盘'),
        ('声音价值对照', 'positive', '声音本身有观看价值'),
        ('继播', 'positive', ''),
        ('旧内容废弃', 'negative', '不能只看故事完整'),
        ('唱歌翻车', 'negative', ''),
    ]):
        profiles.append_experience(tmp_path, {
            'room_id': '123', 'segment_id': str(i), 'revision': 1,
            'experience_type': kind, 'conclusion': kind, 'title': title,
            'review_note': note, 'human_reason': '' if kind == 'positive' else '没意思',
        })
    recommendation = profiles.generate_streamer_recommendation(tmp_path, '123')['recommendations'][-1]
    guidance = recommendation['changes']['approved_guidance']
    for title in ('声音价值对照', '继播', '旧内容废弃', '唱歌翻车'):
        assert f'《{title}》' in guidance
    assert '《较早人工校准》' not in guidance
    assert len(guidance) <= 2000
    assert profiles.read_streamer_profile(tmp_path, '123')['approved_guidance'] == ''
