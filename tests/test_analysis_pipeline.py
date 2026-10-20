from pathlib import Path
import subprocess

import pytest

from bilive.analysis.pipeline import AnalysisPipeline
from bilive.config.settings import Settings
from bilive.db.conn import init_upload_db
from bilive.domain.models import task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.finalize import Finalizer
from bilive.media.remux import _ffprobe_duration
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench
from bilive.upload.queue import UploadQueue
from bilive.vendor.autoslice.analysis_result import AnalysisResult, TrimSuggestion


def test_running_judgment_stage_is_saved_before_remote_call_returns(setup):
    import json
    settings, wb, fin, _ = setup
    settings.analysis.pre_judge_asr = False
    observed = []
    def judge(**kwargs):
        history = json.loads((wb.videos / 'session.mp4.task.json').read_text(encoding='utf-8'))
        observed.extend(c for c in history['candidates'] if c.get('timings', {}).get('judgment', {}).get('status') == 'processing')
        return []
    pipeline = AnalysisPipeline(settings, wb, fin, judge=judge)
    pipeline.run('session.mp4', task_id_from_source_relpath('session.mp4'))
    assert observed
    assert observed[0]['timings']['candidate_cut']['status'] == 'done'
    assert observed[0]['timings']['judgment']['started_at'] > 0


def test_judge_transcript_has_candidate_relative_cue_times():
    settings = Settings()
    pipeline = AnalysisPipeline(settings, None, None)
    transcript = {'segments': [
        {'start': 1.25, 'end': 3.5, 'text': '事件落点'},
        {'start': 4.0, 'end': 8.0, 'text': '谢谢舰长'},
    ]}
    assert pipeline._judge_transcript(transcript) == '1.25–3.50s 事件落点\n4.00–8.00s 谢谢舰长'
    settings.analysis.asr_prompt_max_chars = len('1.25–3.50s 事件落点') + 2
    assert pipeline._judge_transcript(transcript) == '1.25–3.50s 事件落点'
    assert pipeline._judge_transcript(None) == ''


def test_prepare_passes_burst_times_relative_to_candidate(monkeypatch):
    from types import SimpleNamespace
    settings = Settings()
    settings.analysis.pre_judge_asr = False
    observed = []
    pipeline = AnalysisPipeline(settings, None, None, judge=lambda **kw: observed.append(kw) or [])
    monkeypatch.setattr('bilive.analysis.pipeline.ensure_raw_cut', lambda *args: None)
    event = SimpleNamespace(start=4200.0, end=4440.0, burst_start=4310.0, burst_end=4320.0)
    prepared = pipeline._prepare(Path('source.mp4'), 'source.mp4', event, Path('candidate.mp4'), {},
                                [(4312.0, '接梗')], {'display_name': '主播'})
    assert 'error' not in prepared
    assert observed[0]['candidate_start'] == 4200.0
    assert observed[0]['candidate_duration'] == 240.0
    assert observed[0]['candidate_core_start'] == 110.0
    assert observed[0]['candidate_core_end'] == 120.0
    assert observed[0]['danmaku_text'] == '112.00s 接梗'


@pytest.mark.parametrize('pre_judge', [False, True])
def test_auto_analysis_passes_streamer_hotwords_to_asr_and_correction(setup, pre_judge):
    from bilive.vendor.streamer_profiles import patch_streamer_profile
    settings, wb, fin, queue = setup
    settings.analysis.pre_judge_asr = pre_judge
    settings.subtitle.light_correct = True
    source = wb.videos / 'session.mp4'
    named = source.with_name('blive_123_session.mp4')
    source.rename(named)
    source.with_suffix('.xml').rename(named.with_suffix('.xml'))
    patch_streamer_profile(wb.videos, '123', {'display_name': '主播甲',
        'subtitle_hotwords': ['白露', '匹诺康尼'],
        'default_season': {'id':56,'section_id':78,'title':'主播甲合集','section_title':'正片'}})
    calls = []

    def asr(video, **kw):
        calls.append(('asr', kw['hotwords']))
        return {'segments': [{'start': 0, 'end': _ffprobe_duration(video, []), 'text': '白露'}]}

    def correct(lines, **kw):
        calls.append(('correction', kw['hotwords']))
        return lines

    pipeline = AnalysisPipeline(settings, wb, fin, judge=lambda **kw: [result()], transcribe=asr, correct=correct)
    out = pipeline.run(named.name, task_id_from_source_relpath(named.name))
    segment = wb.detail(out['segments'][0])
    assert segment['final_valid'] and segment['state'] == 'awaiting_publish'
    assert any(kind == 'asr' for kind, _ in calls) and any(kind == 'correction' for kind, _ in calls)
    assert all(words == ['主播甲', '白露', '匹诺康尼'] for _, words in calls)
    assert queue.get_by_path(segment['final_path'])['stage'] == 'staged'
    import json
    assert json.loads(queue.get_by_path(segment['final_path'])['season']) == segment['season']
    assert segment['season']['id'] == 56


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('BILIVE_VIDEO_ENCODER', 'libx264')
    videos = tmp_path / "Videos"
    videos.mkdir()
    source = videos / "session.mp4"
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i","testsrc=duration=6:size=160x120:rate=24",
        "-f","lavfi","-i","sine=frequency=440:duration=6","-c:v","libx264","-preset","ultrafast",
        "-c:a","aac",str(source)],check=True,capture_output=True)
    source.with_suffix(".xml").write_text('<i>'+''.join(f'<d p="2.0,1,25">弹幕{x}</d>' for x in range(50))+'</i>', encoding="utf-8")
    jobs = ActionJobStore(tmp_path / "jobs")
    artifacts = tmp_path / "artifacts"
    subs = SubtitleStore(artifacts / "subtitles")
    db = tmp_path / "upload.db"
    init_upload_db(db)
    queue = UploadQueue(db)
    wb = Workbench(videos, artifacts, jobs, subs, queue)
    fin = Finalizer(videos, artifacts, subs)
    settings = Settings()
    settings.burst.window = 1
    settings.burst.context = 2
    settings.subtitle.snap_tolerance = 0
    settings.subtitle.light_correct = False
    return settings, wb, fin, queue


@pytest.mark.parametrize('release_after_task', [True, False])
def test_scene_keeps_one_asr_model_for_candidates_and_formal_recognition(setup, monkeypatch, release_after_task):
    import sys
    import types
    from bilive.vendor.autoslice.mllm_sdk import audio_analyzer as audio

    settings, wb, fin, queue = setup
    settings.subtitle.asr_device = 'cpu'
    settings.subtitle.asr_compute_type = 'int8'
    settings.multi_modal.unload_audio_model_after_analysis = release_after_task
    loads, recognitions, releases = [], [], []

    class Model:
        def __init__(self, *args, **kwargs):
            loads.append(True)

        def transcribe(self, audio_path, **kwargs):
            recognitions.append(audio_path)
            cues = [types.SimpleNamespace(start=.2, end=1.8, text='有效字幕', words=[])]
            if Path(audio_path).read_text() == 'formal':
                cues.append(types.SimpleNamespace(start=2.2, end=3.8, text='另一条字幕', words=[]))
            return iter(cues), types.SimpleNamespace(language='zh')

    fake = types.ModuleType('faster_whisper')
    fake.WhisperModel = Model
    monkeypatch.setitem(sys.modules, 'faster_whisper', fake)
    for key in ('_whisper_model', '_whisper_model_key'):
        monkeypatch.setattr(audio, key, None)
    monkeypatch.setattr(audio, 'release_gpu_memory', lambda: releases.append(len(recognitions)))
    monkeypatch.setattr('bilive.worker.preflight.cached_asr_model', lambda settings: wb.videos)

    def extract(*args, **kwargs):
        path = wb.artifacts / f'audio-{len(recognitions)}.wav'
        path.write_text('formal' if 'duration_seconds' in kwargs else 'candidate')
        return str(path)

    monkeypatch.setattr(audio, 'extract_audio', extract)
    from bilive.vendor.autoslice.burst_detector import BurstEvent
    events = [BurstEvent(a+1, a, a+2, 2, 10, 3, 20, burst_start=a+1, burst_end=a+1.2) for a in (0, 2)]
    monkeypatch.setattr('bilive.analysis.pipeline.detect_bursts', lambda *args, **kwargs: events)
    # Duplicate review is orthogonal to the model lifecycle under test.
    monkeypatch.setattr('bilive.vendor.clip_rules._mark_cross_candidate_duplicates', lambda *args: None)
    pipeline = AnalysisPipeline(settings, wb, fin, judge=lambda **kwargs: [result()])
    output = pipeline.run('session.mp4', task_id_from_source_relpath('session.mp4'))

    assert output['candidates'] == 2
    assert len(recognitions) == 4  # Two candidate calls, then two padded formal calls.
    assert loads == [True]
    assert releases == ([4] if release_after_task else [])
    assert (audio._whisper_model is None) == release_after_task
    assert not list(wb.artifacts.glob('audio-*.wav'))


@pytest.mark.parametrize('entry', ['run', 'transcribe_segment', 'retry_judge'])
def test_asr_task_releases_cached_model_on_exception(monkeypatch, entry):
    from bilive.vendor.autoslice.mllm_sdk import audio_analyzer as audio
    for key in ('_whisper_model', '_whisper_model_key'):
        monkeypatch.setattr(audio, key, object())
    released = []
    monkeypatch.setattr(audio, 'release_gpu_memory', lambda: released.append(True))
    pipeline = AnalysisPipeline(Settings(), None, None)
    with pytest.raises(AttributeError):
        getattr(pipeline, entry)(*(['source.mp4', 'task'] if entry == 'run' else [None]))
    assert released == [True]
    assert audio._whisper_model is None and audio._whisper_model_key is None


def result(start=0.2,end=1.8,quality=0.9):
    return AnalysisResult(title="完整话题",description="描述",quality_score=quality,
        completeness_score=0.9,confidence=0.9,judge_status="keep",suggested_trim=TrimSuggestion(start,end,"完整"))


def test_completed_segment_subtitles_save_during_candidate_preparation(setup, monkeypatch):
    from bilive.domain.models import SubtitleLine
    from bilive.jobs.action_jobs import ActionConflict

    settings, wb, fin, queue = setup
    segment = wb.points('session.mp4', 4, 5.8, '已完成人工片段', '保留依据')
    wb.jobs.submit('start_analysis', task_id_from_source_relpath('session.mp4'))
    pipeline = AnalysisPipeline(settings, wb, fin)
    saved = []

    def prepare(*args):
        # Executes on the actual preparation pool while pipeline.run is active.
        with wb.lock():
            pass
        saved.append(wb.save_subtitles(segment['segment_id'], 1,
                                      [SubtitleLine(1, 0, 1, '人工校对字幕')]))
        return {'results': []}

    monkeypatch.setattr(pipeline, '_prepare', prepare)
    pipeline.run('session.mp4', task_id_from_source_relpath('session.mp4'))
    assert saved[0]['revision'] == 2
    assert wb.subtitles.load(segment['segment_id'])['lines'][0]['text'] == '人工校对字幕'
    with pytest.raises(ActionConflict):
        wb.save_subtitles(segment['segment_id'], 1, [SubtitleLine(1, 0, 1, '过期版本')])


def test_generation_locks_only_its_own_segment(setup, monkeypatch):
    from bilive.domain.models import SubtitleLine
    from bilive.worker.locks import LockUnavailable

    settings, wb, fin, queue = setup
    settings.analysis.pre_judge_asr = False
    wb.jobs.submit('start_analysis', task_id_from_source_relpath('session.mp4'))
    pipeline = AnalysisPipeline(settings, wb, fin, judge=lambda **kw: [result()])
    protected = []

    def formal(source, sid, start, end, *args, **kwargs):
        with wb.lock():
            pass
        with pytest.raises(LockUnavailable):
            wb.save_subtitles(sid, 1, [SubtitleLine(1, 0, 1, '不能覆盖处理中字幕')])
        protected.append(sid)
        return start, end, [SubtitleLine(1, 0, 1, 'AI 字幕')], {}

    monkeypatch.setattr(pipeline, '_formal_subtitles', formal)
    out = pipeline.run('session.mp4', task_id_from_source_relpath('session.mp4'))
    assert protected == out['segments']
    assert wb.detail(protected[0])['final_valid']


def test_subtitle_save_still_blocks_source_recycling(setup):
    from bilive.domain.models import SubtitleLine
    from bilive.jobs.action_jobs import ActionConflict

    settings, wb, fin, queue = setup
    segment = wb.points('session.mp4', 1, 3, '人工片段', '保留依据')
    wb.jobs.submit('trash_source', task_id_from_source_relpath('session.mp4'))
    with pytest.raises(ActionConflict):
        wb.save_subtitles(segment['segment_id'], 1, [SubtitleLine(1, 0, 1, '字幕')])
    assert wb.load(segment['segment_id'])['revision'] == 1


def run(setup, judge, asr=None):
    settings,wb,fin,q = setup
    transcript = lambda video, **window: {"transcript":"字幕","segments":[{"start":0.0,"end":window.get("duration_seconds", _ffprobe_duration(video,[])),"text":"有效字幕"}]}
    pipeline = AnalysisPipeline(settings,wb,fin,judge=judge,transcribe=asr or transcript)
    return pipeline.run("session.mp4",task_id_from_source_relpath("session.mp4"))


def test_real_density_candidate_to_multiple_staged_segments(setup):
    out = run(setup, lambda **kw:[result(),result(2,3.5)])
    settings,wb,fin,q = setup
    assert out["candidates"] == 1 and len(out["segments"]) == 2
    for sid in out["segments"]:
        s = wb.detail(sid)
        assert s["final_valid"] and s["state"] == "awaiting_publish"
        assert q.get_by_path(s["final_path"])["stage"] == "staged"
        for stage in ('formal_asr','finalize','stage'):
            assert s['timings'][stage]['status'] == 'done'
            assert s['timings'][stage]['elapsed_seconds'] >= 0
    assert q.claim_next() is None


def test_analysis_refreshes_default_style_when_worker_is_already_running(setup):
    settings, wb, fin, q = setup
    wb.default_style_provider = lambda: {'font_name': 'Noto Sans SC', 'font_size': 48, 'margin_v': 110, 'outline': 2}
    out = run(setup, lambda **kw: [result()])
    s = wb.detail(out['segments'][0])
    assert s['subtitle_style']['font_size'] == 48 and s['subtitle_style']['margin_v'] == 110
    assert s['final_valid'] and q.get_by_path(s['final_path'])['stage'] == 'staged'
    ass = fin.artifact_paths(s['segment_id'])['ass'].read_text(encoding='utf-8')
    assert 'Style: Default,Noto Sans SC,48,' in ass


def test_completed_recording_through_manual_review_publish_and_source_recycle(setup):
    from types import SimpleNamespace
    from bilive.recorder.events import queue_completed_recording
    from bilive.worker.runner import Worker
    from bilive.lifecycle.review import SourceReview
    from bilive.upload.consumer import UploadConsumer
    settings,wb,fin,q=setup
    room=wb.videos/'123 - 隔离测试';room.mkdir()
    flv=room/'session.flv'
    # Completion events exclude recordings shorter than 30 seconds. This full
    # lifecycle fixture must qualify as a recording rather than a fragment.
    subprocess.run(['ffmpeg','-y','-stream_loop','-1','-i',str(wb.videos/'session.mp4'),
                    '-t','32','-c','copy','-f','flv',str(flv)],check=True,capture_output=True)
    (wb.videos/'session.xml').rename(room/'session.xml')
    (wb.videos/'session.mp4').unlink()
    settings.video.min_source_size_mb=0  # Small synthetic fixture only.
    settings.analysis.pre_judge_asr=False
    settings.edit.font_size=26
    pipeline=AnalysisPipeline(settings,wb,fin,judge=lambda **kw:[result()],transcribe=lambda video, **window:
        {'segments':[{'start':0,'end':1.8,'text':'需要人工修订'}]})
    recycled=wb.artifacts/'isolated-recycled';recycled.mkdir(parents=True)
    moved=[]
    def move(paths):
        for path in paths:
            moved.append(path);path.rename(recycled/f'{len(moved)}-{path.name}')
    lifecycle=SourceReview(wb,move)
    worker=Worker(wb.jobs,fin,wb.artifacts/'worker.lock',upload_queue=q,workbench=wb,
                  analysis=pipeline,lifecycle=lifecycle,preflight=lambda j:(True,''))
    import os,shutil
    peer=wb.videos/'456 - 第二房间';peer.mkdir()
    peer_flv=peer/'session.flv';shutil.copy2(flv,peer_flv);shutil.copy2(room/'session.xml',peer/'session.xml')
    if os.environ.get('BILIVE_NATIVE_ACCEPTANCE') == '1':
        (wb.videos.parent/'.bilive-isolated-acceptance').touch()
        project=Path(__file__).resolve().parents[1]
        native=project/'.venv-recorder/Scripts/python.exe'
        completed=subprocess.run([str(native),str(project/'tests/fixtures/native_event_emitter.py'),
            str(wb.videos),str(wb.jobs.root),str(flv),str(peer_flv)],check=True,capture_output=True,text=True)
        assert 'NATIVE_EVENT_CENTER_PASS' in completed.stdout
    else:
        assert queue_completed_recording(wb.videos,wb.jobs,str(flv))
        assert queue_completed_recording(wb.videos,wb.jobs,str(peer_flv))
    assert worker.run_once() and not flv.exists()
    source=flv.with_suffix('.mp4');assert source.is_file()
    assert worker.run_once()  # Finish the second local remux.
    assert not wb.jobs.busy() and wb.list('123 - 隔离测试/session.mp4') == []
    from bilive.analysis.tasks import RecordingTasks
    for rel in ('123 - 隔离测试/session.mp4', '456 - 第二房间/session.mp4'):
        RecordingTasks(wb).queue(task_id_from_source_relpath(rel))
    for _ in range(2):assert worker.run_once()  # Explicitly selected analyses.
    assert not wb.jobs.busy()
    segments=wb.list('123 - 隔离测试/session.mp4');assert len(segments) == 1
    s=wb.detail(segments[0]['segment_id']);assert s['final_valid']
    assert s['subtitle_style']['font_size'] == 26
    calls=[]
    client=SimpleNamespace(check_login=lambda:{'isLogin':True},
        upload_file=lambda *args:calls.append('upload') or 'confirmed-isolated-remote',
        upload_cover=lambda path:'https://example.test/isolated-cover.jpg',
        submit_uploaded_video=lambda *args:calls.append('publish') or {'code':0,'data':{'bvid':'BVisolated'}})
    settings.upload.delete_after_success=False
    consumer=UploadConsumer(q,settings.upload,client,wb.artifacts/'upload.lock')
    assert consumer.process_one()['status'] == 'idle' and calls == []
    s=wb.save_style(s['segment_id'],s['revision'],{'font_size':32})
    assert q.get_by_path(str(fin.artifact_paths(s['segment_id'])['final'])) is None
    wb.submit('reburn',s['segment_id'],s['revision']);assert worker.run_once()
    s=wb.detail(s['segment_id']);assert s['final_valid']
    wb.submit('approve_publish',s['segment_id'],s['revision']);assert worker.run_once()
    assert consumer.process_one()['status'] == 'published' and calls == ['upload','publish']
    final=Path(wb.load(s['segment_id'])['final_path'])
    task=task_id_from_source_relpath('123 - 隔离测试/session.mp4')
    review=lifecycle.finish(task,note='完整隔离链路人工确认')
    assert review['review_saved'] and not review['recycle_queued']
    assert lifecycle.queue(task)['queued'] and worker.run_once()
    assert not source.exists() and not source.with_suffix('.xml').exists()
    assert not Path(s['candidate_path']).exists() and final.is_file()
    assert wb.subtitles.load(s['segment_id'])['lines'] and lifecycle.load(task)['trash_status'] == 'done'
    assert q.get_by_path(str(final))['bvid'] == 'BVisolated'
    assert consumer.process_one()['status'] == 'idle' and calls == ['upload','publish']
    peer_source=peer_flv.with_suffix('.mp4')
    other=wb.detail(wb.list('456 - 第二房间/session.mp4')[0]['segment_id'])
    assert peer_source.is_file() and peer_source.with_suffix('.xml').is_file()
    assert Path(other['candidate_path']).is_file() and Path(other['final_path']).is_file()
    assert not other['publish_approval'] and q.get_by_path(other['final_path'])['stage'] == 'staged'
    assert wb.subtitles.load(other['segment_id'])['lines']
    other_task=task_id_from_source_relpath('456 - 第二房间/session.mp4')
    assert lifecycle.load(other_task)['review_state'] == 'unprocessed'
    wb.submit('approve_publish',other['segment_id'],other['revision']);assert worker.run_once()
    assert consumer.process_one()['status'] == 'published'
    assert lifecycle.finish(other_task,note='第二房间独立终审')['review_saved']
    assert lifecycle.queue(other_task)['queued'] and worker.run_once()
    assert not peer_source.exists() and Path(other['final_path']).is_file() and final.is_file()
    assert lifecycle.load(task)['trash_status'] == lifecycle.load(other_task)['trash_status'] == 'done'
    assert calls == ['upload','publish','upload','publish']


def test_low_score_never_runs_formal_asr_or_renders(setup):
    setup[0].analysis.pre_judge_asr = False
    calls = []
    out = run(setup, lambda **kw:[result(quality=0.5)], lambda video, **window:calls.append(video))
    wb = setup[1]
    s = wb.detail(out["segments"][0])
    assert s["decision"] == "review" and not s["final_valid"]
    assert calls == [] and Path(s["candidate_path"]).is_file()


def test_empty_result_is_not_drop(setup):
    out = run(setup, lambda **kw:[])
    assert out["requires_source_review"]
    assert list(setup[1].artifacts.glob("candidates/*/*.mp4"))


def test_model_empty_response_saves_reason_and_raw_evidence_without_manual_review(setup):
    import json
    from bilive.vendor.autoslice.mllm_sdk.mimo_video import _analysis_list_from_mimo_dict
    response = {'clips': [], 'empty_reason': '没有可独立成片的内容'}
    results = _analysis_list_from_mimo_dict(response, artist='主播', model='offline-fixture')
    out = run(setup, lambda **kw: results)
    history = json.loads((setup[1].videos / 'session.mp4.task.json').read_text(encoding='utf-8'))
    assert out['segments'] == [] and not out['requires_source_review']
    assert history['candidates'][0]['state'] == 'empty'
    assert history['candidates'][0]['reason'] == response['empty_reason']
    assert history['candidates'][0]['raw_model_response'] == response
    assert list(setup[1].artifacts.glob('candidates/*/*.mp4'))


def test_formal_asr_failure_keeps_candidate_and_review_record(setup):
    setup[0].analysis.pre_judge_asr = False
    out = run(setup, lambda **kw:[result()], lambda video, **window:{"segments":[]})
    s = setup[1].detail(out["segments"][0])
    assert not s["final_valid"] and s["processing_error"] == "PipelineError"
    assert s['timings']['formal_asr']['status'] == 'failed'
    assert 'finalize' not in s['timings']
    assert Path(s["candidate_path"]).is_file()


def test_shared_candidate_with_drop_and_keep_is_preserved(setup):
    dropped = result()
    dropped.judge_status = "drop"
    dropped.retain_recommendation = False
    dropped.raw_model_response = {"decision":"drop"}
    out = run(setup, lambda **kw:[dropped,result(2,3.5)])
    assert list(setup[1].artifacts.glob("candidates/*/*.mp4"))
    assert len(out["segments"]) == 2

def test_room_profile_reaches_judge_metadata_and_burned_style(setup):
    import json
    from bilive.vendor import streamer_profiles as profiles
    settings,wb,fin,q=setup
    old=wb.videos/'session.mp4';room=wb.videos/'123 - 甲';room.mkdir()
    old.rename(room/'session.mp4');old.with_suffix('.xml').rename(room/'session.xml')
    profiles.patch_streamer_profile(wb.videos,'123',{'display_name':'主播甲','approved_guidance':'保留完整话题',
        'default_tags':['标签甲'],'default_description':'档案简介','default_subtitle_style':{'font_size':32,'margin_v':80}})
    seen=[]
    def judge(**kwargs):
        seen.append(kwargs);r=result();r.description='';return [r]
    pipeline=AnalysisPipeline(settings,wb,fin,judge=judge,transcribe=lambda video, **kwargs:
        {'segments':[{'start':0,'end':kwargs.get('duration_seconds', 4),'text':'字幕依据'}]})
    rel='123 - 甲/session.mp4';out=pipeline.run(rel,task_id_from_source_relpath(rel))
    assert seen[0]['artist'] == '主播甲' and seen[0]['guidance'] == '保留完整话题'
    s=wb.detail(out['segments'][0]);row=q.get_by_path(s['final_path'])
    assert row['description'] == '档案简介'
    assert '标签甲' in row['tags']
    paths=fin.artifact_paths(s['segment_id'])
    assert 'Style: Default,Noto Sans SC,32,' in paths['ass'].read_text(encoding='utf-8')
    assert json.loads(paths['meta'].read_text(encoding='utf-8'))['subtitle_style']['font_size'] == 32


@pytest.mark.parametrize('override', [{}, {'outline': 3}] + [
    dict(font_name='Douyin Sans', font_size=48, bold=True, spacing=5.76,
         primary_colour=colour, outline_colour='&H00FFFFFF', outline=2,
         shadow_colour=shadow, shadow=3, shadow_outline=10)
    for colour, shadow in [('&H005959D9', '&H00ADADEE'), ('&H00D99859', '&H00EECBAD')]])
def test_analysis_inherits_global_style_after_profile_style_save(setup, override):
    import json
    from bilive.vendor import streamer_profiles as profiles
    settings, wb, fin, q = setup
    old = wb.videos / 'session.mp4'
    room = wb.videos / '123 - test'
    room.mkdir()
    old.rename(room / 'session.mp4')
    old.with_suffix('.xml').rename(room / 'session.xml')
    profiles.patch_streamer_profile(wb.videos, '123', {'default_subtitle_style': override})
    global_style = {'font_name': 'Microsoft YaHei', 'font_size': 80, 'margin_v': 110, 'outline': 2}
    wb.default_style_provider = lambda: dict(global_style)
    pipeline = AnalysisPipeline(settings, wb, fin, judge=lambda **kw: [result()],
        transcribe=lambda video, **kw: {'segments': [{'start': 0, 'end': 4, 'text': '字幕依据'}]})
    rel = '123 - test/session.mp4'
    out = pipeline.run(rel, task_id_from_source_relpath(rel))
    segment = wb.detail(out['segments'][0])
    assert segment['subtitle_style'] == {**global_style, **override}
    assert segment['final_valid'] and q.get_by_path(segment['final_path'])['stage'] == 'staged'
    paths = fin.artifact_paths(segment['segment_id'])
    assert f"Style: Default,{override.get('font_name', 'Microsoft YaHei')},{override.get('font_size', 80)}," in paths['ass'].read_text(encoding='utf-8')
    assert json.loads(paths['meta'].read_text(encoding='utf-8'))['subtitle_style'] == segment['subtitle_style']


def test_room_density_overrides_are_isolated(setup,monkeypatch):
    from bilive.vendor import streamer_profiles as profiles
    from bilive.analysis import pipeline as module
    settings,wb,fin,q=setup
    old=wb.videos/'session.mp4';room=wb.videos/'123 - 甲';room.mkdir()
    import shutil
    shutil.copy2(old,room/'session.mp4');shutil.copy2(old.with_suffix('.xml'),room/'session.xml')
    profiles.patch_streamer_profile(wb.videos,'123',{'default_slice_options':{'burst_ratio':4,'burst_context':30}})
    calls=[]
    monkeypatch.setattr(module,'detect_bursts',lambda *args,**kw:calls.append(kw) or [])
    pipeline=AnalysisPipeline(settings,wb,fin,judge=lambda **kw:[])
    for rel in ('123 - 甲/session.mp4','session.mp4'):pipeline.run(rel,task_id_from_source_relpath(rel))
    assert calls[0]['burst_ratio'] == 4 and calls[0]['context'] == 30
    assert calls[1]['burst_ratio'] == settings.burst.ratio and calls[1]['context'] == settings.burst.context

def event(start,end):
    from bilive.vendor.autoslice.burst_detector import BurstEvent
    return BurstEvent((start+end)/2,start,end,end-start,10,4,50,burst_start=start,burst_end=end)


@pytest.mark.parametrize('snap_enabled',[True,False])
@pytest.mark.parametrize('cue_layout', ['single_sentence', 'chat_context'])
def test_padding_snap_and_subtitle_offset_use_real_media(setup,monkeypatch,snap_enabled,cue_layout):
    import json
    from bilive.analysis import pipeline as module
    settings,wb,fin,q=setup
    settings.analysis.pre_judge_asr=False;settings.subtitle.padding=1;settings.subtitle.snap_tolerance=0.4
    settings.subtitle.snap_enabled=snap_enabled
    expected_start,expected_end=(1.3,3.8) if snap_enabled else (1.5,3.5)
    expected_duration=expected_end-expected_start
    # A changing pitch identifies the audio origin; a constant sine cannot.
    source=wb.videos/'session.mp4';fresh=wb.videos/'audio-fixture.mp4'
    subprocess.run(['ffmpeg','-y','-i',str(source),'-f','lavfi','-i',
        'aevalsrc=sin(2*PI*(200*t+40*t*t)):s=16000:d=6','-map','0:v:0','-map','1:a:0',
        '-c:v','copy','-c:a','aac',str(fresh)],check=True,capture_output=True)
    fresh.replace(source)
    monkeypatch.setattr(module,'detect_bursts',lambda *a,**kw:[event(0,6)])
    seen=[]
    def asr(video, **window):
        seen.append(window['duration_seconds'])
        if cue_layout == 'chat_context':
            # Inner boundaries are closer, but must not erase the setup/response.
            return {'segments': [
                {'start': .8, 'end': 1.05, 'text': '事情的起因'},
                {'start': 1.1, 'end': 2.9, 'text': '中间的互动'},
                {'start': 2.95, 'end': 3.3, 'text': '最后的回应'},
                {'start': 3.4, 'end': 3.8, 'text': '下一个无关话题'},
            ]}
        return {'segments':[{'start':0.8,'end':3.3,'text':'完整句子'}]}
    pipeline=AnalysisPipeline(settings,wb,fin,judge=lambda **kw:[result(1.5,3.5)],transcribe=asr)
    out=pipeline.run('session.mp4',task_id_from_source_relpath('session.mp4'))
    s=wb.detail(out['segments'][0])
    assert s['final_valid'] and abs(seen[0]-4) < 0.08
    assert s['source_start'] == pytest.approx(expected_start) and s['source_end'] == pytest.approx(expected_end)
    assert s['subtitles'][0]['start'] == pytest.approx(0)
    assert s['subtitles'][-1]['end'] == pytest.approx(expected_duration)
    if cue_layout == 'chat_context':
        assert [line['text'] for line in s['subtitles']] == ['事情的起因', '中间的互动', '最后的回应']
        assert s['subtitles'][1]['start'] == pytest.approx(1.6-expected_start)
        assert all(0 <= line['start'] < line['end'] <= expected_duration for line in s['subtitles'])
    assert abs(_ffprobe_duration(Path(s['final_path']),[])-expected_duration) < 0.08
    meta=json.loads(fin.artifact_paths(s['segment_id'])['meta'].read_text(encoding='utf-8'))
    assert meta['source_start'] == pytest.approx(expected_start) and meta['source_end'] == pytest.approx(expected_end)
    assert s['alignment']['asr_origin'] == .5
    # The raw picture above the subtitle area must match the chosen fractional source point.
    def pixels(path,at):
        return subprocess.run(['ffmpeg','-v','error','-ss',str(at),'-i',str(path),'-frames:v','1',
            '-vf','crop=160:50:0:0','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],check=True,capture_output=True).stdout
    source_frame=pixels(wb.videos/'session.mp4',expected_start);raw_frame=pixels(fin.artifact_paths(s['segment_id'])['raw'],0)
    assert len(source_frame) == len(raw_frame) == 160*50*3
    assert sum(abs(a-b) for a,b in zip(source_frame,raw_frame))/len(source_frame) < 8
    from array import array
    import math
    def samples(path,at):
        completed=subprocess.run(['ffmpeg','-v','error','-ss',str(at),'-i',str(path),'-t','0.6',
            '-vn','-ac','1','-ar','16000','-f','f32le','pipe:1'],capture_output=True)
        assert completed.returncode == 0,completed.stderr.decode('utf-8',errors='replace')
        data=completed.stdout
        values=array('f');values.frombytes(data);return values
    reference=samples(source,expected_start+.2)
    for media in (fin.artifact_paths(s['segment_id'])['raw'],Path(s['final_path'])):
        actual=samples(media,.2);length=min(len(actual),len(reference));xs=reference[256:length-256]
        scores=[]
        for lag in range(-128,129,8):
            ys=actual[256+lag:length-256+lag]
            dot=sum(x*y for x,y in zip(xs,ys))
            score=dot/math.sqrt(sum(x*x for x in xs)*sum(y*y for y in ys))
            scores.append((score,lag))
        score,lag=max(scores)
        assert score > .95 and abs(lag) <= 128,(score,lag)
    assert q.get_by_path(s['final_path'])['stage'] == 'staged'


@pytest.mark.parametrize('workers',[1,3])
def test_configured_request_concurrency_is_real_and_bounded(setup,monkeypatch,workers):
    import threading,time
    from bilive.analysis import pipeline as module
    settings,wb,fin,q=setup
    settings.analysis.pre_judge_asr=False;settings.mimo.request_concurrency=workers
    monkeypatch.setattr(module,'detect_bursts',lambda *a,**kw:[event(0,2),event(2,4),event(4,6)])
    barrier=threading.Barrier(workers);lock=threading.Lock();active=0;peak=0
    def judge(**kw):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        try:
            barrier.wait(timeout=15);time.sleep(.05);return [result(.2,1.5,.5)]
        finally:
            with lock:active-=1
    pipeline=AnalysisPipeline(settings,wb,fin,judge=judge,transcribe=lambda video, **window:pytest.fail('low score must not ASR'))
    out=pipeline.run('session.mp4',task_id_from_source_relpath('session.mp4'))
    assert peak == workers and out['candidates'] == 3 and out['requires_source_review']
    assert len(wb.list('session.mp4')) == 3 and q.claim_next() is None


def test_cross_candidate_duplicate_chooses_score_before_formal_asr(setup,monkeypatch):
    from bilive.analysis import pipeline as module
    settings,wb,fin,q=setup;settings.analysis.pre_judge_asr=False
    monkeypatch.setattr(module,'detect_bursts',lambda *a,**kw:[event(0,4),event(1,5)])
    def judge(**kw):return [result(1,3,.85)] if kw['candidate_start'] == 0 else [result(.2,2.2,.98)]
    asr_calls=[]
    def asr(video, **window):
        asr_calls.append(video);return {'segments':[{'start':0,'end':6,'text':'字幕'}]}
    pipeline=AnalysisPipeline(settings,wb,fin,judge=judge,transcribe=asr)
    out=pipeline.run('session.mp4',task_id_from_source_relpath('session.mp4'))
    segments=wb.list('session.mp4');winner=next(s for s in segments if s['final_path']);loser=next(s for s in segments if not s['final_path'])
    assert len(asr_calls) == 1 and winner['quality_score'] == .98
    assert loser['decision'] == 'review' and loser['duplicate_of'] == winner['segment_id']
    assert Path(loser['candidate_path']).is_file() and out['requires_source_review']


@pytest.mark.parametrize('timestamps',[
    [{'start':float('nan'),'end':1,'text':'无效'}],
    [{'start':0,'end':99,'text':'越界'}],
    [{'start':0,'end':1,'text':''}],
])
def test_bad_formal_timestamps_preserve_review_material(setup,timestamps):
    setup[0].analysis.pre_judge_asr=False
    out=run(setup,lambda **kw:[result()],lambda video, **window:{'segments':timestamps})
    s=setup[1].detail(out['segments'][0])
    assert s['processing_error'] == 'PipelineError' and not s['final_valid']
    assert Path(s['candidate_path']).exists() and setup[3].claim_next() is None


def test_failed_or_timestamp_changing_correction_keeps_original(setup,monkeypatch):
    from bilive.analysis import pipeline as module
    settings,wb,fin,q=setup;settings.analysis.pre_judge_asr=False;settings.subtitle.light_correct=True
    monkeypatch.setattr(module,'detect_bursts',lambda *a,**kw:[event(0,6)])
    def correct(segments,**kw):
        segments[0].start=99;return segments
    pipeline=AnalysisPipeline(settings,wb,fin,judge=lambda **kw:[result(1,3)],
        transcribe=lambda video, **window:{'segments':[{'start':0,'end':6,'text':'原始字幕'}]},correct=correct)
    out=pipeline.run('session.mp4',task_id_from_source_relpath('session.mp4'));s=wb.detail(out['segments'][0])
    assert s['final_valid'] and s['subtitles'][0]['text'] == '原始字幕' and s['subtitles'][0]['start'] == 0
    assert s['alignment']['correction'] == 'failed_original_retained'

def test_invalid_auxiliary_asr_does_not_block_valid_formal_pass(setup,monkeypatch):
    from bilive.analysis import pipeline as module
    settings,wb,fin,q=setup
    monkeypatch.setattr(module,'detect_bursts',lambda *a,**kw:[event(0,6)])
    calls=[]
    def asr(video, **window):
        calls.append(video.name)
        return {'error':'auxiliary failed','segments':[]} if video.name.startswith('candidate-') else {'segments':[{'start':0,'end':6,'text':'正式字幕'}]}
    out=run(setup,lambda **kw:[result(1,3)],asr)
    assert len(calls) == 2 and wb.detail(out['segments'][0])['final_valid']
    import json
    history=json.loads((wb.videos/'session.mp4.task.json').read_text(encoding='utf-8'))
    assert history['candidates'][0]['pre_asr_failed']


def test_successful_light_correction_is_text_only_and_persisted(setup,monkeypatch):
    import json
    from bilive.analysis import pipeline as module
    from bilive.vendor.autoslice.analysis_result import TranscriptSegment
    settings,wb,fin,q=setup;settings.analysis.pre_judge_asr=False;settings.subtitle.light_correct=True
    monkeypatch.setattr(module,'detect_bursts',lambda *a,**kw:[event(0,6)])
    pipeline=AnalysisPipeline(settings,wb,fin,judge=lambda **kw:[result(1,3)],
        transcribe=lambda video, **window:{'segments':[{'start':0,'end':6,'text':'专名错字'}]},
        correct=lambda segments,**kw:[TranscriptSegment(v.start,v.end,'正确专名') for v in segments])
    out=pipeline.run('session.mp4',task_id_from_source_relpath('session.mp4'));s=wb.detail(out['segments'][0])
    assert s['subtitles'][0]['text'] == '正确专名' and s['subtitles'][0]['start'] == 0
    sidecar=json.loads((fin.artifact_paths(s['segment_id'])['meta'].with_name('analysis_sidecar.json')).read_text(encoding='utf-8'))
    assert sidecar['transcript'] == '正确专名' and sidecar['source_start'] == 1
    assert sidecar['processing_stage'] == 'completed' and sidecar['alignment']['correction'] == 'completed'

def test_alignment_created_duplicate_retains_subtitles_but_does_not_render(setup,monkeypatch):
    from bilive.analysis import pipeline as module
    settings,wb,fin,q=setup;settings.analysis.pre_judge_asr=False;settings.subtitle.snap_tolerance=3
    monkeypatch.setattr(module,'detect_bursts',lambda *a,**kw:[event(0,6)])
    pipeline=AnalysisPipeline(settings,wb,fin,judge=lambda **kw:[result(1.5,2.5),result(3,4)],
        transcribe=lambda video, **window:{'segments':[{'start':0,'end':6,'text':'话题字幕'}]})
    out=pipeline.run('session.mp4',task_id_from_source_relpath('session.mp4'))
    segments=wb.list('session.mp4');loser=next(s for s in segments if s['processing_stage'] == 'duplicate_review')
    assert loser['decision'] == 'review' and loser['final_path'] is None
    assert wb.detail(loser['segment_id'])['subtitles'] and Path(loser['candidate_path']).exists()
    assert q.list(stage='staged')['total'] == 1 and q.claim_next() is None
    assert out['requires_source_review']
