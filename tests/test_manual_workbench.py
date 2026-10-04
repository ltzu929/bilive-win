"""Isolated API → persisted jobs → real FFmpeg → staging/approval regression."""
import importlib
import multiprocessing
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bilive.config.settings import Settings
from bilive.db.conn import init_upload_db
from bilive.domain.models import task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.finalize import Finalizer, PipelineError
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench
from bilive.upload.queue import UploadQueue
from bilive.worker.runner import Worker


@pytest.fixture
def env(tmp_path, monkeypatch):
    videos = tmp_path / "Videos"
    videos.mkdir()
    source = videos / "session.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=5:size=160x120:rate=24",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source)],
                   check=True, capture_output=True)
    artifacts = tmp_path / "artifacts"
    jobs = ActionJobStore(tmp_path / "jobs")
    subs = SubtitleStore(artifacts / "subtitles")
    db = tmp_path / "uploads.db"
    init_upload_db(db)
    uploads = UploadQueue(db)
    wb = Workbench(videos, artifacts, jobs, subs, uploads)
    fin = Finalizer(videos, artifacts, subs)
    worker = Worker(jobs, fin, tmp_path / "worker.lock", upload_queue=uploads, workbench=wb)
    api = importlib.import_module("bilive.api.app")
    settings = Settings()
    settings.paths.videos_dir = videos
    settings.paths.db_path = db
    for name, value in {"settings":settings, "jobs":jobs, "subtitles":subs,
                        "upload_queue":uploads, "workbench":wb}.items():
        monkeypatch.setattr(api, name, value)
    from types import SimpleNamespace
    monkeypatch.setattr(api,"worker_control",SimpleNamespace(wake=lambda:{"state":"offline"},status=lambda:{"state":"offline"}))
    return TestClient(api.app), worker, wb, uploads


def create(client, start=0.5, end=2.5):
    r = client.post("/api/segments/point", json={"source_relpath":"session.mp4",
                    "source_start":start, "source_end":end, "title":"人工片段", "note":"完整话题"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize('with_final', [False, True])
def test_discard_is_persisted_withdraws_staging_and_blocks_actions(env, with_final):
    from bilive.domain.models import ActionJob
    c, worker, wb, uploads = env
    s = create(c)
    if with_final:
        s = subtitles(c, s)
        wb.submit('finalize', s['segment_id'], s['revision'])
        assert worker.run_once()
        s = wb.detail(s['segment_id'])
        assert s['final_valid']
    old_final = s.get('final_path')
    response = c.post(f"/api/segments/{s['segment_id']}/drop", json={
        'expected_revision':s['revision'], 'reason':'没有必要切片'})
    assert response.status_code == 200, response.text
    dropped = wb.detail(s['segment_id'])
    assert dropped['state'] == 'dropped' and dropped['decision'] == 'drop'
    assert dropped['drop_reason'] == '没有必要切片' and not dropped['final_valid']
    assert (wb.videos/'session.mp4').is_file()
    if old_final:
        assert uploads.get_by_path(old_final) is None and Path(old_final).is_file()
    for action in ('finalize','render','reburn','approve_publish','retry_judge','transcribe_subtitles'):
        response = c.post('/api/actions',json={'action':action,'target':s['segment_id'],'revision':dropped['revision']})
        assert response.status_code == 409
    response = c.post('/api/subtitles/save',json={'segment_id':s['segment_id'],
        'expected_revision':dropped['revision'],'duration':2,'lines':[{'start':0,'end':1,'text':'不能复活'}]})
    assert response.status_code == 409
    bypass = ActionJob(job_id='test', action='finalize', target=s['segment_id'],
                       payload={'revision':dropped['revision']}, created_at='')
    with pytest.raises(PipelineError, match='discarded'):
        wb.execute(bypass, worker.finalizer)


def subtitles(client, s, text="第一版"):
    r = client.post("/api/subtitles/save", json={"segment_id":s["segment_id"],
                    "expected_revision":s["revision"], "duration":2,
                    "lines":[{"start":0.1,"end":1.8,"text":text}]})
    assert r.status_code == 200, r.text
    return r.json()


def finalize(client, worker, s):
    r = client.post("/api/actions", json={"action":"finalize","target":s["segment_id"],"revision":s["revision"]})
    assert r.status_code == 200, r.text
    assert worker.run_once()
    j = client.get(f"/api/actions/{r.json()['job_id']}").json()
    assert j["status"] == "done", j
    return client.get(f"/api/segments/{s['segment_id']}").json()


def test_keep_confirms_decision_without_invalidating_subtitles_or_final(env):
    c, worker, wb, uploads = env
    s = finalize(c, worker, subtitles(c, create(c)))
    stored = wb.load(s['segment_id'])
    stored.update(decision='review', state='needs_review')
    wb.save(stored)
    before = Path(s['final_path']).read_bytes()
    response = c.post(f"/api/segments/{s['segment_id']}/keep", json={'expected_revision': s['revision']})
    assert response.status_code == 200, response.text
    kept = response.json()
    assert kept['human_reviewed'] and kept['decision'] == 'manual_keep'
    assert kept['revision'] == s['revision'] and kept['subtitles'] == s['subtitles']
    assert kept['final_valid'] and Path(kept['final_path']).read_bytes() == before
    assert uploads.get_by_path(kept['final_path'])['stage'] == 'staged'
    assert not kept['publish_approval']


@pytest.mark.parametrize('blocked', ['stale', 'dropped', 'approved', 'active', 'source_active'])
def test_keep_respects_existing_edit_boundaries(env, blocked):
    c, worker, wb, uploads = env
    s = create(c)
    expected = s['revision']
    if blocked == 'stale':
        expected -= 1
    elif blocked == 'dropped':
        wb.drop(s['segment_id'], expected, '不要此片段')
        expected += 1
    elif blocked == 'approved':
        s['publish_approval'] = True
        wb.save(s)
    elif blocked == 'active':
        wb.submit('transcribe_subtitles', s['segment_id'], expected)
    else:
        jobs = wb.jobs
        jobs.submit('start_analysis', task_id_from_source_relpath('session.mp4'), {})
    before = wb.load(s['segment_id'])
    response = c.post(f"/api/segments/{s['segment_id']}/keep", json={'expected_revision': expected})
    assert response.status_code == 409, response.text
    assert wb.load(s['segment_id']) == before


def test_local_transcription_returns_relative_draft_and_preserves_saved_media(env):
    from bilive.analysis.pipeline import AnalysisPipeline
    c, w, wb, q = env
    s = finalize(c, w, subtitles(c, create(c)))
    final = Path(s['final_path']).read_bytes()
    calls = []
    def transcribe(source, **window):
        calls.append((source, window))
        return {'segments': [{'start': 0.1, 'end': 1.8, 'text': '本地识别'}]}
    w.analysis = AnalysisPipeline(Settings(), wb, w.finalizer, transcribe=transcribe)
    w.preflight = lambda job: (True, '')
    req = {'action': 'transcribe_subtitles', 'target': s['segment_id'], 'revision': s['revision']}
    queued = c.post('/api/actions', json=req)
    assert queued.status_code == 200, queued.text
    assert c.post('/api/actions', json=req).json()['job_id'] == queued.json()['job_id']
    assert c.post('/api/subtitles/save', json={'segment_id': s['segment_id'],
        'expected_revision': s['revision'], 'duration': 2, 'lines': [{'start': 0, 'end': 1, 'text': '冲突'}]}).status_code == 409
    assert w.run_once()
    job = c.get(f"/api/actions/{queued.json()['job_id']}").json()
    assert job['status'] == 'done', job
    assert calls == [(wb.videos / 'session.mp4', {'start_seconds': 0.5, 'duration_seconds': 2})]
    assert job['result']['lines'] == [{'index': 1, 'start': 0.1, 'end': 1.8, 'text': '本地识别'}]
    detail = c.get(f"/api/segments/{s['segment_id']}").json()
    assert detail['transcription_job']['result'] == job['result']
    assert detail['revision'] == s['revision'] and detail['subtitles'] == s['subtitles']
    assert detail['final_valid'] and Path(s['final_path']).read_bytes() == final
    assert q.get_by_path(s['final_path'])['stage'] == 'staged'


@pytest.mark.parametrize('segments', [[], [{'start': 0, 'end': 20, 'text': '越界'}],
    [{'start': float('nan'), 'end': 1, 'text': '无效'}]])
def test_local_transcription_failure_preserves_existing_subtitles(env, segments):
    from bilive.analysis.pipeline import AnalysisPipeline
    c, w, wb, q = env
    s = subtitles(c, create(c))
    w.analysis = AnalysisPipeline(Settings(), wb, w.finalizer,
        transcribe=lambda source, **window: {'segments': segments})
    w.preflight = lambda job: (True, '')
    queued = c.post('/api/actions', json={'action': 'transcribe_subtitles',
        'target': s['segment_id'], 'revision': s['revision']}).json()
    assert w.run_once()
    assert c.get(f"/api/actions/{queued['job_id']}").json()['status'] == 'failed'
    detail = wb.detail(s['segment_id'])
    assert detail['revision'] == s['revision'] and detail['subtitles'] == wb.subtitles.load(s['segment_id'])['lines']


def test_local_transcription_reports_adapter_error(env):
    from bilive.analysis.pipeline import AnalysisPipeline
    c, w, wb, q = env
    s = create(c)
    w.analysis = AnalysisPipeline(Settings(), wb, w.finalizer,
        transcribe=lambda source, **window: {'segments': [], 'error': 'decoder compatibility error'})
    w.preflight = lambda job: (True, '')
    queued = c.post('/api/actions', json={'action': 'transcribe_subtitles',
        'target': s['segment_id'], 'revision': s['revision']}).json()
    assert w.run_once()
    job = c.get(f"/api/actions/{queued['job_id']}").json()
    assert job['status'] == 'failed' and 'decoder compatibility error' in job['result']['error']


def test_empty_segment_can_transcribe_but_stale_or_approved_segment_cannot(env):
    c, w, wb, q = env
    s = create(c)
    req = {'action': 'transcribe_subtitles', 'target': s['segment_id'], 'revision': s['revision']}
    assert c.post('/api/actions', json={**req, 'revision': s['revision']+1}).status_code == 409
    s['publish_approval'] = True
    wb.save(s)
    assert c.post('/api/actions', json=req).status_code == 409
    s['publish_approval'] = False
    wb.save(s)
    assert c.post('/api/actions', json=req).status_code == 200


def test_full_manual_flow_and_range_stream(env):
    c, w, wb, q = env
    recordings = c.get("/api/recordings").json()
    assert recordings["total"] == 1
    task = recordings["items"][0]["task_id"]
    media = c.get(f"/api/media/source/{task}", headers={"Range":"bytes=0-99"})
    assert media.status_code == 206 and len(media.content) == 100
    s = subtitles(c, create(c))
    duplicate = {"action":"finalize","target":s["segment_id"],"revision":s["revision"]}
    j1, j2 = c.post("/api/actions", json=duplicate), c.post("/api/actions", json=duplicate)
    assert j1.json()["job_id"] == j2.json()["job_id"]
    assert c.post("/api/subtitles/save", json={"segment_id":s["segment_id"],"duration":2,
        "expected_revision":s["revision"],"lines":[{"start":0,"end":1,"text":"conflict"}]}).status_code == 409
    assert w.run_once()
    s = c.get(f"/api/segments/{s['segment_id']}").json()
    assert s["final_valid"] and s["state"] == "awaiting_publish"
    assert q.get_by_path(s["final_path"])["stage"] == "staged"
    assert q.claim_next() is None
    assert c.get(f"/api/media/final/{s['segment_id']}").status_code == 200
    approve = c.post("/api/actions", json={"action":"approve_publish","target":s["segment_id"],"revision":s["revision"]})
    assert approve.status_code == 200 and q.get_by_path(s["final_path"])["stage"] == "staged"
    assert w.run_once()
    assert q.get_by_path(s["final_path"])["stage"] == "queued"
    assert c.post("/api/subtitles/save", json={"segment_id":s["segment_id"],"duration":2,
        "expected_revision":s["revision"],"lines":[{"start":0,"end":1,"text":"blocked"}]}).status_code == 409


def test_edit_withdraws_staged_and_invalidates_preview(env):
    c, w, wb, q = env
    s = finalize(c, w, subtitles(c, create(c)))
    old = Path(s["final_path"])
    changed = subtitles(c, s, "第二版字幕")
    assert old.is_file() and q.get_by_path(str(old)) is None
    assert c.get(f"/api/media/final/{s['segment_id']}").status_code == 409
    stale = c.post("/api/actions", json={"action":"approve_publish","target":s["segment_id"],"revision":s["revision"]})
    assert stale.status_code == 409
    new = finalize(c, w, changed)
    assert new["final_valid"] and new["revision"] > s["revision"]
    assert "第二版字幕" in wb.subtitles.load(s["segment_id"])["lines"][0]["text"]


@pytest.mark.parametrize('has_remote',[False,True])
def test_failed_upload_protects_current_final_and_edits(env,has_remote):
    c,w,wb,q=env;s=finalize(c,w,subtitles(c,create(c)))
    path=s['final_path'];q.activate(path)
    if has_remote:q.mark_uploaded(path,'confirmed-remote')
    q.mark_failed(path,'isolated failure',has_remote=has_remote)
    before=Path(path).read_bytes();saved=wb.load(s['segment_id'])
    response=c.post('/api/subtitles/save',json={'segment_id':s['segment_id'],'expected_revision':s['revision'],
        'duration':2,'lines':[{'start':0,'end':1,'text':'forbidden edit'}]})
    assert response.status_code == 409
    assert c.post(f"/api/segments/{s['segment_id']}/drop",json={'expected_revision':s['revision'],'reason':'drop'}).status_code == 409
    assert wb.load(s['segment_id']) == saved and Path(path).read_bytes() == before


def test_partial_reburn_does_not_replace_existing_successful_media(env,monkeypatch):
    c,w,wb,q=env;s=finalize(c,w,subtitles(c,create(c)))
    paths=w.finalizer.artifact_paths(s['segment_id']);before={name:paths[name].read_bytes() for name in ('raw','final')}
    changed=subtitles(c,s,'saved edit survives partial render')
    media=importlib.import_module('bilive.media.finalize')
    def partial_output(cmd,log):
        Path(cmd[-1]).write_bytes(b'nonempty incomplete mp4')
    monkeypatch.setattr(media,'_run_ffmpeg',partial_output)
    response=c.post('/api/actions',json={'action':'reburn','target':s['segment_id'],'revision':changed['revision']})
    assert response.status_code == 200 and w.run_once()
    assert c.get(f"/api/actions/{response.json()['job_id']}").json()['status'] == 'failed'
    assert all(paths[name].read_bytes()==value for name,value in before.items())
    assert not wb.detail(s['segment_id'])['final_valid']
    assert wb.subtitles.load(s['segment_id'])['lines'][0]['text'] == 'saved edit survives partial render'


def test_range_keeps_identity_and_replaces_cached_cut(env):
    c, w, wb, q = env
    s = finalize(c, w, subtitles(c, create(c)))
    r = c.post("/api/segments/point", json={"segment_id":s["segment_id"],"expected_revision":s["revision"],
        "source_relpath":"session.mp4","source_start":2,"source_end":4.5,"title":"改范围","note":"补充结尾"})
    assert r.status_code == 200 and r.json()["segment_id"] == s["segment_id"]
    updated = finalize(c, w, subtitles(c, r.json()))
    meta = w.finalizer.artifact_paths(s["segment_id"])["raw"].with_suffix(".range.json")
    import json
    assert json.loads(meta.read_text())["start"] == 2
    result = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0", updated["final_path"]], capture_output=True,text=True,check=True)
    assert abs(float(result.stdout)-2.5) < 0.15


def test_invalid_paths_missing_db_and_closed_legacy_routes(env):
    c, w, wb, q = env
    assert c.post("/api/segments/point", json={"source_relpath":"../outside.mp4",
        "source_start":0,"source_end":1,"note":"x"}).status_code == 422
    escaped = task_id_from_source_relpath("../outside.mp4")
    assert c.get(f"/api/media/source/{escaped}").status_code == 422
    assert c.get("/api/segments/not-real").status_code == 404
    assert c.post("/api/uploads/activate", json={"video_path":"arbitrary.mp4"}).status_code == 410
    q.db_path.unlink()
    assert c.get("/api/uploads").status_code == 503
    assert not q.db_path.exists()


def test_failed_burn_keeps_saved_edits_and_reuses_raw_on_retry(env, monkeypatch):
    c, w, wb, q = env
    s = subtitles(c, create(c), "失败后保留的字幕")
    media = importlib.import_module("bilive.media.finalize")
    original = media.burn_subtitles
    def fail_burn(*args):
        raise media.PipelineError("isolated burn failure")
    monkeypatch.setattr(media, "burn_subtitles", fail_burn)
    request = {"action":"finalize", "target":s["segment_id"], "revision":s["revision"]}
    job = c.post("/api/actions", json=request).json()
    assert w.run_once()
    failed = c.get(f"/api/actions/{job['job_id']}").json()
    assert failed["status"] == "failed"
    assert wb.detail(s['segment_id'])['timings']['manual_finalize']['status'] == 'failed'
    raw = w.finalizer.artifact_paths(s["segment_id"])["raw"]
    timestamp = raw.stat().st_mtime_ns
    assert wb.subtitles.load(s["segment_id"])["lines"][0]["text"] == "失败后保留的字幕"
    assert wb.source("session.mp4").is_file()
    assert not wb.detail(s["segment_id"])["final_valid"]
    monkeypatch.setattr(media, "burn_subtitles", original)
    assert finalize(c, w, s)["final_valid"]
    assert raw.stat().st_mtime_ns == timestamp


def _submit_process(root, out):
    try:
        j = ActionJobStore(Path(root)).submit("finalize", "shared-segment", {"revision":1})
        out.put(j.job_id)
    except Exception as exc:
        out.put(repr(exc))


@pytest.mark.parametrize('round_number', range(5))
def test_process_submissions_share_one_job(tmp_path, round_number):
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    processes = [ctx.Process(target=_submit_process, args=(str(tmp_path / "jobs"), out)) for _ in range(2)]
    for p in processes: p.start()
    for p in processes:
        p.join(15)
        assert p.exitcode == 0
    assert out.get(timeout=2) == out.get(timeout=2)
    assert len(list((tmp_path / "jobs").glob("*.pending.json"))) == 1

def test_style_save_invalidates_stage_keeps_subtitles_and_reburns(env):
    import json
    c,w,wb,q=env
    s=finalize(c,w,subtitles(c,create(c)))
    final=Path(s['final_path']);old=final.read_bytes();lines=s['subtitles']
    url=f"/api/segments/{s['segment_id']}/style"
    response=c.post(url,json={'expected_revision':s['revision'],'style':{'font_size':64,'margin_v':80}})
    assert response.status_code == 200,response.text
    saved=response.json()
    assert saved['revision'] == s['revision']+1 and saved['subtitles_revision'] == saved['revision']
    assert q.get_by_path(str(final)) is None and final.read_bytes() == old
    detail=c.get(f"/api/segments/{s['segment_id']}").json()
    assert not detail['final_valid'] and detail['subtitles'] == lines
    assert c.post(url,json={'expected_revision':s['revision'],'style':{}}).status_code == 409
    updated=finalize(c,w,saved)
    assert final.read_bytes() != old
    assert 'Style: Default,Noto Sans SC,64,' in w.finalizer.artifact_paths(s['segment_id'])['ass'].read_text(encoding='utf-8')
    assert q.get_by_path(str(final))['stage'] == 'staged'
    approved=c.post('/api/actions',json={'action':'approve_publish','target':s['segment_id'],'revision':updated['revision']})
    assert approved.status_code == 200 and w.run_once()
    assert c.post(url,json={'expected_revision':updated['revision'],'style':{}}).status_code == 409


def test_style_and_manual_edit_reject_active_source_analysis(env):
    c,w,wb,q=env;s=after=subtitles(c,create(c))
    task=task_id_from_source_relpath('session.mp4')
    wb.jobs.submit('start_analysis',task,{})
    assert c.post(f"/api/segments/{s['segment_id']}/style",json={'expected_revision':s['revision'],'style':{}}).status_code == 409
    assert c.post('/api/segments/point',json={'source_relpath':'session.mp4','source_start':3,'source_end':4,'title':'新片段','note':'人工'}).status_code == 409
    assert c.post('/api/actions',json={'action':'finalize','target':s['segment_id'],'revision':s['revision']}).status_code == 409


@pytest.mark.parametrize('invalid_range', [False, True])
def test_retry_judge_calls_original_candidate_and_preserves_edits(env, invalid_range):
    from bilive.analysis.pipeline import AnalysisPipeline
    from bilive.vendor.autoslice.analysis_result import AnalysisResult,TrimSuggestion
    c,w,wb,q=env;s=finalize(c,w,subtitles(c,create(c)))
    task=task_id_from_source_relpath('session.mp4')
    candidate=wb.artifacts/'candidates'/task/'candidate-0-5000.mp4';candidate.parent.mkdir(parents=True);candidate.write_bytes((wb.videos/'session.mp4').read_bytes())
    (wb.videos/'session.xml').write_text('<i><d p="1,1,25">弹幕</d></i>',encoding='utf-8')
    stored=wb.load(s['segment_id']);stored.update(candidate_path=str(candidate),candidate_start=0,candidate_end=5,core_start=1,core_end=2);wb.save(stored)
    settings=Settings();settings.analysis.pre_judge_asr=False
    calls=[]
    def judge(**kw):
        calls.append(kw);return [AnalysisResult('新依据','描述',quality_score=.9,completeness_score=.9,confidence=.9,suggested_trim=TrimSuggestion(float('nan') if invalid_range else 1,3,'完整'))]
    w.analysis=AnalysisPipeline(settings,wb,w.finalizer,judge=judge);w.preflight=lambda job:(True,'')
    response=c.post('/api/actions',json={'action':'retry_judge','target':s['segment_id'],'revision':s['revision']})
    assert response.status_code == 200,response.text
    assert c.post(f"/api/segments/{s['segment_id']}/style",json={'expected_revision':s['revision'],'style':{}}).status_code == 409
    assert w.run_once()
    detail=c.get(f"/api/segments/{s['segment_id']}").json()
    assert len(calls) == 1 and calls[0]['video_path'] == str(candidate)
    assert detail['latest_rejudgment']['results'][0]['title'] == '新依据'
    if invalid_range:
        result=detail['latest_rejudgment']['results'][0]
        assert result['judge_status'] == 'review' and not result['retain_recommendation']
        assert result['suggested_trim']['trim_start'] is None and result['judge_error'] == 'invalid judgment range'
    assert detail['revision'] == s['revision'] and detail['subtitles'] == s['subtitles'] and detail['final_valid']
    assert detail['source_start'] == s['source_start'] and q.get_by_path(s['final_path'])['stage'] == 'staged'
    assert c.get('/api/actions/'+response.json()['job_id']).json()['result']['requires_manual_application']


def test_global_style_defaults_apply_to_new_manual_and_partial_style(env):
    c,w,wb,q=env
    wb.default_style={'font_name':'Global Font','font_size':28,'margin_v':72}
    s=create(c)
    assert s['subtitle_style'] == wb.default_style
    saved=c.post(f"/api/segments/{s['segment_id']}/style",json={'expected_revision':s['revision'],'style':{'font_size':32}})
    assert saved.status_code == 200
    assert saved.json()['subtitle_style']['font_name'] == 'Global Font'
    assert saved.json()['subtitle_style']['margin_v'] == 72


@pytest.mark.parametrize('table',['upload_queue','upload_history'])
def test_upload_read_api_returns_unavailable_for_missing_table(env,table):
    import sqlite3
    c,w,wb,q=env
    with sqlite3.connect(q.db_path) as conn:
        conn.execute(f'DROP TABLE {table}')
    assert c.get('/api/uploads').status_code == 503


def _crash_worker(videos,artifacts,jobs_root,db,phase):
    import os
    jobs=ActionJobStore(Path(jobs_root));subs=SubtitleStore(Path(artifacts)/'subtitles')
    queue=UploadQueue(Path(db));wb=Workbench(Path(videos),Path(artifacts),jobs,subs,queue)
    original=queue.remove_staged if phase == 'withdraw' else queue.activate
    def interrupted(*args,**kwargs):
        original(*args,**kwargs)
        os._exit(77)
    setattr(queue,'remove_staged' if phase == 'withdraw' else 'activate',interrupted)
    Worker(jobs,Finalizer(wb.videos,wb.artifacts,subs),wb.artifacts/'crash-worker.lock',
           workbench=wb,upload_queue=queue,preflight=lambda j:(True,'')).run_once()


@pytest.mark.parametrize('phase',['withdraw','activate','published_cleanup'])
def test_real_process_crash_recovers_same_job_without_losing_stage_or_approval(env,phase):
    from bilive.domain.models import ActionJobStatus
    c,w,wb,q=env;s=finalize(c,w,subtitles(c,create(c)))
    action='reburn' if phase == 'withdraw' else 'approve_publish'
    accepted=c.post('/api/actions',json={'action':action,'target':s['segment_id'],'revision':s['revision']}).json()
    ctx=multiprocessing.get_context('spawn')
    process=ctx.Process(target=_crash_worker,args=(str(wb.videos),str(wb.artifacts),str(wb.jobs.root),str(q.db_path),phase))
    process.start();process.join(25)
    assert process.exitcode == 77
    assert len(wb.jobs.valid_jobs(ActionJobStatus.PROCESSING)) == 1
    assert not wb.jobs.owner_alive(wb.jobs.valid_jobs(ActionJobStatus.PROCESSING)[0][1].owner)
    assert q.get_by_path(s['final_path']) is None if phase == 'withdraw' else q.get_by_path(s['final_path'])['stage'] == 'queued'
    if phase == 'published_cleanup':
        q.mark_published(s['final_path'],'BVisolated-confirmed')
        Path(s['final_path']).unlink()  # Consumer completed while approval Worker was down.
    assert w.run_once()
    result=c.get('/api/actions/'+accepted['job_id']).json()
    assert result['status'] == 'done',result
    recovered=wb.detail(s['segment_id'])
    assert recovered['revision'] == s['revision'] and recovered['final_valid'] == (phase != 'published_cleanup')
    assert recovered['publish_approval'] == (phase != 'withdraw')
    assert q.get_by_path(s['final_path'])['stage'] == {'withdraw':'staged','activate':'queued','published_cleanup':'published'}[phase]
    if phase == 'published_cleanup':
        from bilive.lifecycle.review import SourceReview
        assert SourceReview(wb).finish(task_id_from_source_relpath('session.mp4'))['review_saved']
        return  # Finish intentionally queued an isolated source recycle action.
    assert wb.source('session.mp4').is_file() and not wb.jobs.busy()


def test_recording_evidence_is_paginated_read_only_and_shows_existing_artifacts(env):
    import json
    c,w,wb,q=env;s=finalize(c,w,subtitles(c,create(c)))
    history=wb.videos/'session.mp4.task.json'
    history.write_text(json.dumps({'status':'done','candidates':[{'start':i,'end':i+1,'state':'review',
        'peak_density':2.5,'danmaku_count':50,'reason':'missing scores'} for i in range(25)]}))
    before={path:path.read_bytes() for path in [history,wb.root/f"{s['segment_id']}.json",wb.subtitles._path(s['segment_id'])]}
    q.db_path.unlink()
    task=task_id_from_source_relpath('session.mp4')
    response=c.get(f'/api/recordings/{task}/evidence?limit=20&offset=20')
    assert response.status_code == 200,response.text
    data=response.json()
    assert data['total'] == 25 and len(data['candidates']) == 5 and data['candidates'][0]['start'] == 20
    assert data['source_size'] > 0 and data['related_files_size'] > data['source_size']
    assert any(a['name'] == 'final_output.mp4' for a in data['segments'][0]['artifacts'])
    assert all(path.read_bytes() == original for path,original in before.items()) and not q.db_path.exists()
    assert c.get(f'/api/recordings/{task}/evidence?limit=101').status_code == 422
