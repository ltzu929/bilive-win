from __future__ import annotations

import subprocess
import shutil
from pathlib import Path

import pytest

from bilive.media.chat_trim import chat_kind, detect_chat_cuts, suggest_cuts
from bilive.media.chat_preview import preview_path
from bilive.domain.models import task_id_from_source_relpath
from bilive.lifecycle.review import SourceReview
from bilive.media.cut_timeline import kept_ranges, output_time
from test_finalize_pipeline import _frame, _white_pixels
from test_manual_workbench import create, env, finalize


CUES = [{'index': 1, 'start': 3, 'end': 4.5, 'text': 'FIRST'},
        {'index': 2, 'start': 11, 'end': 12.5, 'text': 'SECOND'}]


def make_chat_source(source, motion=False):
    video = ("color=c=red:s=320x240:r=25:d=20,"
             "drawbox=c=green:t=fill:enable='gte(t,12)'")
    if motion:
        video += ",drawbox=x=90:y=50:w=75:h=65:c=white:t=fill:enable='between(t,8,9)'"
    audio = ("aevalsrc=if(between(t\\,4\\,5.5)\\,0.18*sin(2*PI*440*t)\\,"
             "if(between(t\\,12\\,13.5)\\,0.18*sin(2*PI*880*t)\\,0.003*sin(2*PI*128*t))):s=48000:d=20")
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', video,
                    '-f', 'lavfi', '-i', audio, '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-c:a', 'aac', str(source)], capture_output=True, check=True)


@pytest.mark.parametrize('protection', ['movement', 'danmaku', 'speech', 'focus', 'prompt'])
def test_meaningful_pause_is_preserved(protection):
    lines = [dict(v) for v in CUES]
    if protection == 'prompt':
        lines[0]['text'] = '你们猜猜？'
    cuts = suggest_cuts(18, lines, [(5, 10.4)] if protection == 'speech' else [],
                        [8] if protection == 'movement' else [],
                        [(10.7, '哈哈哈')] if protection == 'danmaku' else [],
                        8 if protection == 'focus' else None)
    assert [c['kind'] for c in cuts] == ['head', 'tail']
    assert all(not (a['start'] < l['end'] and a['end'] > l['start']) for a in cuts for l in lines)


def test_short_gaps_and_manual_or_legacy_classification(tmp_path):
    assert suggest_cuts(6, [dict(CUES[0], start=1, end=2), dict(CUES[1], start=4, end=5)], [], [], []) == []
    assert chat_kind({}, tmp_path) == 'unknown'
    (tmp_path/'analysis_sidecar.json').write_text('{"clip_type":"chat_interaction"}', encoding='utf-8')
    assert chat_kind({}, tmp_path) == 'chat'
    assert chat_kind({'content_type':'singing', 'clip_type':'chat'}, tmp_path) == 'other'


def test_visual_and_chat_evidence_from_source(tmp_path):
    pytest.importorskip('faster_whisper.vad')
    source = tmp_path / 'scene.mp4'
    make_chat_source(source, motion=True)
    # A single laugh near the end protects the tail, movement protects the middle.
    source.with_suffix('.xml').write_text('<i><d p="16.5,1">哈哈</d></i>', encoding='utf-8')
    result = detect_chat_cuts(source, {'source_start':1,'source_end':19}, CUES)
    assert [cut['kind'] for cut in result] == ['head']


def check_second_cue(path, cuts):
    import numpy as np
    duration = output_time(18, cuts)
    timing = output_time(11, cuts)
    actual = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=nw=1:nk=1', str(path)]))
    assert actual == pytest.approx(duration, abs=.09)
    before, during = _frame(str(path), timing-.25), _frame(str(path), timing+.25)
    assert before[0] > 200 and not _white_pixels(before)
    assert during[1] > 90 and during[0] < 30 and _white_pixels(during)
    sound = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(timing+.25), '-i', str(path),
        '-t', '0.2', '-ar', '16000', '-ac', '1', '-f', 's16le', 'pipe:1'])
    wave = np.frombuffer(sound, dtype='<i2')
    assert np.argmax(np.abs(np.fft.rfft(wave))) * 16000/len(wave) == pytest.approx(880, abs=8)


@pytest.mark.parametrize('encoder', ['libx264', pytest.param('h264_nvenc',
    marks=pytest.mark.skipif(not shutil.which('nvidia-smi'), reason='NVIDIA hardware unavailable'))])
def test_preview_restore_apply_and_reburn(env, monkeypatch, encoder):
    pytest.importorskip('faster_whisper.vad')
    monkeypatch.setenv('BILIVE_VIDEO_ENCODER', encoder)
    client, worker, wb, uploads = env
    make_chat_source(wb.videos/'session.mp4')
    s = create(client, 1, 19)
    sid = s['segment_id']
    s = wb.save_style(sid, s['revision'], {'font_name':'Arial','font_size':96})
    s = client.post('/api/subtitles/save', json={'segment_id':sid,'duration':18,
        'expected_revision':s['revision'],'lines':CUES}).json()
    s = finalize(client, worker, s)
    original = Path(s['final_path']).read_bytes()
    version = s['revision']

    def preview(options=None):
        response = client.post('/api/actions', json={'action':'refine_chat','target':sid,
                               'revision':version,'payload':options or {}})
        assert response.status_code == 200, response.text
        assert worker.run_once()
        job = client.get('/api/actions/'+response.json()['job_id']).json()
        assert job['status'] == 'done', job['result']
        detail = client.get('/api/segments/'+sid).json()
        assert detail['revision'] == version
        return detail['chat_draft']

    draft = preview()
    assert [c['kind'] for c in draft['cuts']] == ['head','pause','tail']
    endpoint = '/api/media/chat-preview/'+sid+'/'
    response = client.get(endpoint+draft['id'], headers={'Range':'bytes=0-31'})
    assert response.status_code == 206 and len(response.content) == 32
    assert wb.valid_final(wb.load(sid)) and Path(s['final_path']).read_bytes() == original
    first = draft['id']
    check_second_cue(preview_path(wb.artifacts, s, first), [[c['start'], c['end']] for c in draft['cuts']])
    draft = preview({'preview_id':first, 'toggle':1})
    assert [c['enabled'] for c in draft['cuts']] == [True,False,True]
    assert client.get(endpoint+first).status_code == 409
    assert client.post('/api/segments/'+sid+'/chat-edit', json={
        'expected_revision':version, 'preview_id':first}).status_code == 409
    applied = client.post('/api/segments/'+sid+'/chat-edit', json={
        'expected_revision':version,'preview_id':draft['id']})
    assert applied.status_code == 200, applied.text
    s = applied.json()
    assert not s['final_valid'] and uploads.get_by_path(str(wb.artifacts/sid/'final_output.mp4')) is None
    assert s['subtitles'][1]['start'] == 11 and s['chat_draft']['revision'] == s['revision']
    s = finalize(client, worker, s)
    cuts = s['chat_cuts']
    duration = output_time(18, cuts)
    assert 10 < duration < 12
    preview_video = preview_path(wb.artifacts, s, draft['id'])
    manifest = SourceReview(wb).manifest(s['source_relpath'], task_id_from_source_relpath(s['source_relpath']))
    assert any(row['root'] == 'candidates' and row['relpath'].endswith(preview_video.name) for row in manifest)
    for path in [preview_video, Path(s['final_path'])]:
        check_second_cue(path, cuts)
    assert wb.record_preview(sid,s['revision'],[[0,duration]])['complete']
    # A new line inside an applied head cut keeps that speech on the next burn.
    s = client.post('/api/subtitles/save', json={'segment_id':sid, 'duration':18,
        'expected_revision':s['revision'],'lines':[{'start':.1,'end':.7,'text':'Intro'},*CUES]}).json()
    assert s['chat_cuts'] == cuts[1:] and 'chat_draft' not in wb.detail(sid)
    assert kept_ranges(18, s['chat_cuts'])[0][0] == 0
