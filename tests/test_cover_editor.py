from pathlib import Path
from io import BytesIO
import base64
import subprocess

from PIL import Image

from bilive.analysis.pipeline import AnalysisPipeline
from bilive.vendor.autoslice.analysis_result import AnalysisResult
from bilive.vendor.autoslice.mllm_sdk.mimo_video import _analysis_list_from_mimo_dict
from test_manual_workbench import create, env, finalize, subtitles


def test_original_download_keeps_native_size_with_existing_jpeg(env):
    client, worker, wb, _ = env
    source = wb.videos / 'session.mp4'
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc=size=2048x256:rate=4:duration=3', '-pix_fmt', 'yuv420p', str(source)], check=True)
    segment = create(client)
    sid = segment['segment_id']
    folder = wb.artifacts / sid / 'cover-frames'
    folder.mkdir(parents=True)
    Image.new('RGB', (1920, 240)).save(folder / '1250.jpg')
    url = f'/api/segments/{sid}/frames/1250?original=true'
    assert client.get(url).status_code == 409
    job = client.post('/api/actions', json=dict(action='cover_frames', target=sid,
                      revision=segment['revision'], payload={'source_time': 1.25})).json()
    assert worker.run_once()
    assert client.get('/api/actions/' + job['job_id']).json()['status'] == 'done'
    download = client.get(url)
    image = Image.open(BytesIO(download.content))
    assert download.status_code == 200 and image.format == 'PNG' and image.size == (2048, 256)
    assert 'attachment' in download.headers['content-disposition']
    assert wb.load(sid).get('cover_path') is None


def test_returned_cover_is_only_adopted_when_draft_is_saved(env):
    client, worker, _, queue = env
    segment = finalize(client, worker, subtitles(client, create(client)))
    url = '/api/segments/' + segment['segment_id']
    image = BytesIO()
    Image.new('RGB', (1600, 900), 'gold').save(image, 'PNG')
    cover = {'upload': base64.b64encode(image.getvalue()).decode('ascii')}
    preview = client.post(url + '/cover-preview', json=cover)
    assert preview.status_code == 200
    assert client.get(url).json()['cover_path'] == segment['cover_path']
    saved = client.post(url + '/metadata', json={'expected_revision': segment['revision'],
                        'title': segment['title'], 'cover': cover})
    assert saved.status_code == 200 and saved.json()['cover_design']['uploaded']
    assert client.get(url + '/cover').content == preview.content
    row = queue.get_by_path(segment['final_path'])
    assert row['stage'] == 'staged' and Path(row['cover_path']).read_bytes() == preview.content


def test_select_frame_save_and_rebuild_staged_draft(env):
    client, worker, wb, queue = env
    segment = finalize(client, worker, subtitles(client, create(client)))
    sid, rev = segment['segment_id'], segment['revision']
    path = Path(segment['final_path'])
    original = path.read_bytes()
    args = dict(action='cover_frames', target=sid, revision=rev, payload={'source_time': 1.25})
    job = client.post('/api/actions', json=args).json()
    assert worker.run_once()
    frames = client.get('/api/actions/' + job['job_id']).json()['result']['frames']
    assert frames == [750, 1250, 1750]
    assert wb.detail(sid)['cover_frames'] == frames
    assert client.get(f'/api/segments/{sid}/frames/1250').status_code == 200
    recipe = {'frame_ms': 1250, 'caption': '展示', 'secondary': {'x': .8, 'zoom': 2}}
    preview = client.post(f'/api/segments/{sid}/cover-preview', json=recipe)
    assert preview.status_code == 200
    data = dict(expected_revision=rev, title='展示实物', editorial_angle='展示组装细节', cover=recipe)
    saved = client.post(f'/api/segments/{sid}/metadata', json=data).json()
    assert saved['editorial_angle'] == data['editorial_angle'] and saved['final_valid']
    assert client.post(f'/api/segments/{sid}/metadata', json=data).status_code == 409
    assert client.get(f'/api/segments/{sid}/cover').content == preview.content
    row = queue.get_by_path(str(path))
    assert row['stage'] == 'staged' and Path(row['cover_path']).read_bytes() == preview.content
    assert path.read_bytes() == original
    rebuilt = finalize(client, worker, saved)
    assert rebuilt['cover_path'] == saved['selected_cover']
    assert queue.get_by_path(rebuilt['final_path'])['cover_path'] == rebuilt['cover_path']
    narrowed = wb.points('session.mp4', 1.5, 2.5, '新范围', '', sid, rebuilt['revision'])
    assert 'selected_cover' not in narrowed and 'cover_path' not in narrowed
    assert client.post(f'/api/segments/{sid}/cover-preview', json=recipe).status_code == 422


def test_optional_model_cover_evidence_keeps_candidate_and_source_clocks_distinct():
    raw = {'angle': '展示自己组装的娃娃', 'evidence': '把娃娃展示在画面右侧',
           'frame': 28, 'start': 20, 'end': 40, 'caption': '看起来是不是还不错'}
    clip = dict(decision='keep', reason='展示实物', title='娃娃', description='展示',
                tags=['手作'], trim_start=10, trim_end=80, core_start=21, core_end=32,
                quality_score=.9, completeness_score=.9, confidence=.9, editorial_suggestion=raw)
    result = _analysis_list_from_mimo_dict({'clips': [clip]}, artist='咩栗', model='test')[0]
    restored = AnalysisResult.from_dict(result.to_dict())
    suggestion = AnalysisPipeline._editorial_suggestion(restored, 1602, 1612, 1682)
    assert suggestion == {**raw, 'start': 1622, 'end': 1642, 'frame': 1630}
    assert AnalysisPipeline._editorial_suggestion(restored, 1602, 1640, 1682) is None
    for value in [None, True, float('nan'), '28']:
        restored.editorial_suggestion = dict(raw, frame=value)
        assert AnalysisPipeline._editorial_suggestion(restored, 1602, 1612, 1682) is None
