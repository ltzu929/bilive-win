import asyncio
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bilive.config.settings import Settings
from bilive.db.conn import init_upload_db
from bilive.domain.models import ActionJobStatus, task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionJobStore, ActionConflict
from bilive.media.finalize import Finalizer
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench
from bilive.upload.queue import UploadQueue
from bilive.worker.runner import Worker
from bilive.recorder.events import queue_completed_recording, install_recording_events
from bilive.recorder.client import RecorderClient, RecorderUnavailable
from bilive.recorder.server import configure
from bilive.recorder.cookie import persistent_settings_payload
from bilive.recorder.gateway import RecorderGateway
from bilive.recorder.monitor import install_monitor_controls
from bilive.analysis.tasks import RecordingTasks


@pytest.fixture
def setup(tmp_path):
    videos = tmp_path / 'Videos';videos.mkdir()
    artifacts = tmp_path / 'artifacts'
    jobs = ActionJobStore(tmp_path / 'jobs')
    subs = SubtitleStore(artifacts / 'subtitles')
    db = tmp_path / 'uploads.db';init_upload_db(db)
    wb = Workbench(videos, artifacts, jobs, subs, UploadQueue(db))
    return wb


def test_completed_events_preserve_paths_and_isolate_rooms(setup):
    wb = setup
    ids = []
    for room in ('123 - 主播甲','456 - 主播乙'):
        source = wb.videos / room / 'session.flv';source.parent.mkdir();source.write_bytes(b'source')
        first = queue_completed_recording(wb.videos, wb.jobs, str(source))
        second = queue_completed_recording(wb.videos, wb.jobs, str(source))
        assert first.job_id == second.job_id
        ids.append(first.target)
        assert source.read_bytes() == b'source'
    assert len(set(ids)) == 2
    with pytest.raises(ValueError):
        queue_completed_recording(wb.videos, wb.jobs, str(wb.videos.parent / 'outside.flv'))
    injected = wb.videos / '123 - 主播甲/session_injecting.flv';injected.write_bytes(b'temp')
    assert queue_completed_recording(wb.videos, wb.jobs, str(injected)) is None


def test_native_event_hook_subscribes_disposes_and_retains_job_on_failed_wake(setup):
    wb = setup
    source = wb.videos / '123 - 主播/session.flv';source.parent.mkdir();source.write_bytes(b'source')
    callbacks, disposed, wakes = [], [], []
    center = SimpleNamespace(events=SimpleNamespace(subscribe=lambda cb:
        callbacks.append(cb) or SimpleNamespace(dispose=lambda:disposed.append(True))))
    def wake():
        wakes.append(True);raise OSError('isolated missing Worker')
    app = FastAPI()
    install_recording_events(app, wb.videos, wb.jobs, SimpleNamespace(wake=wake), center)
    with TestClient(app):
        callbacks[0](SimpleNamespace(type='VideoFileCreatedEvent',data=SimpleNamespace(path=str(source))))
        assert not wb.jobs.busy()
        callbacks[0](SimpleNamespace(type='VideoPostprocessingCompletedEvent',data=SimpleNamespace(path=str(source))))
        import time
        for _ in range(100):
            if wakes:break
            time.sleep(.01)
        assert wakes
        assert len(wb.jobs.valid_jobs(ActionJobStatus.PENDING)) == 1
    assert disposed == [True] and source.exists()


def test_real_remux_waits_for_manual_analysis_and_recovers_after_flv_deletion(setup):
    wb = setup
    source = wb.videos / '123 - 主播/session.flv';source.parent.mkdir()
    subprocess.run(['ffmpeg','-y','-f','lavfi','-i','testsrc=duration=31:size=160x120:rate=24',
                    '-f','lavfi','-i','sine=frequency=440:duration=31','-c:v','libx264','-preset','ultrafast',
                    '-c:a','aac','-f','flv',str(source)],capture_output=True,check=True)
    xml = source.with_suffix('.xml');xml.write_text('<i/>')
    job = queue_completed_recording(wb.videos,wb.jobs,str(source))
    worker = Worker(wb.jobs, Finalizer(wb.videos,wb.artifacts,wb.subtitles),wb.artifacts/'worker.lock',
                    upload_queue=wb.uploads,workbench=wb,preflight=lambda job:(True,''))
    assert worker.run_once()
    assert not source.exists() and source.with_suffix('.mp4').exists() and xml.exists()
    assert not source.with_suffix('.mp4.pending').exists()
    assert wb.jobs.valid_jobs(ActionJobStatus.PENDING) == []
    # Same action resumes verified output without relying on a nonexistent FLV.
    assert worker.execute(job)['analysis_job_id'] is None
    assert wb.jobs.valid_jobs(ActionJobStatus.PENDING) == []
    # Selecting this MP4 and explicitly starting analysis remains available.
    task = task_id_from_source_relpath(source.with_suffix('.mp4').relative_to(wb.videos).as_posix())
    pending = RecordingTasks(wb).queue(task)
    assert pending.action == 'start_analysis' and pending.target == task
    assert len(wb.jobs.valid_jobs(ActionJobStatus.PENDING)) == 1


def test_auto_analysis_reads_saved_setting_and_cancels_without_model(setup, monkeypatch):
    from bilive.config.settings import load_settings, save_analysis_auto_start
    wb = setup
    source = wb.videos / 'session.mp4';source.write_bytes(b'local recording')
    config = wb.videos.parent / 'config.toml'
    config.write_text('[slice.analysis]\npre_judge_asr=false\n', encoding='utf-8')
    worker = Worker(wb.jobs, Finalizer(wb.videos,wb.artifacts,wb.subtitles),wb.artifacts/'worker.lock',
        workbench=wb, auto_analysis_enabled=lambda:load_settings(config).analysis.auto_start)
    import bilive.media.remux as remux
    monkeypatch.setattr(remux, 'remux_recording', lambda path:SimpleNamespace(mp4_path=str(source),verified=True))
    job = wb.jobs.submit('remux_recording',task_id_from_source_relpath('session.flv'),{})
    assert worker.execute(job)['analysis_job_id'] is None
    save_analysis_auto_start(config, True)
    queued_id = worker.execute(job)['analysis_job_id']
    assert queued_id and source.with_suffix('.mp4.pending').is_file()
    pending = wb.jobs.valid_jobs(ActionJobStatus.PENDING)
    automatic = next(j for _,j in pending if j.job_id == queued_id)
    assert automatic.payload['automatic'] is True
    save_analysis_auto_start(config, False)
    assert worker.execute(automatic)['cancelled']
    assert not source.with_suffix('.mp4.pending').exists()


def test_cancel_skip_and_processing_controls_do_not_authorize_review(setup):
    wb = setup
    source = wb.videos/'session.mp4';source.write_bytes(b'isolated')
    task = task_id_from_source_relpath('session.mp4');tasks=RecordingTasks(wb)
    first = tasks.queue(task)
    assert tasks.queue(task).job_id == first.job_id
    tasks.control(task,'cancel')
    assert source.exists() and not source.with_suffix('.mp4.pending').exists()
    second = tasks.queue(task);assert second.job_id != first.job_id
    processing = wb.jobs.claim('test')
    for action in ('cancel','skip'):
        with pytest.raises(ActionConflict):tasks.control(task,action)
    wb.jobs.fail(processing,{'error':'isolated'})
    assert tasks.control(task,'skip')['review_complete'] is False
    assert source.with_suffix('.mp4.done').exists()


def test_small_source_skips_without_model_and_does_not_complete_review(setup):
    wb=setup
    source=wb.videos/'small.mp4';source.write_bytes(b'isolated')
    task=task_id_from_source_relpath('small.mp4');tasks=RecordingTasks(wb)
    queued=tasks.queue(task)
    from bilive.analysis.pipeline import AnalysisPipeline
    settings=Settings();settings.paths.videos_dir=wb.videos
    pipeline=AnalysisPipeline(settings,wb,Finalizer(wb.videos,wb.artifacts,wb.subtitles))
    from bilive.worker.preflight import check_action
    assert check_action(queued,settings) == (True,'')
    assert tasks.execute(queued,pipeline)['skipped']
    data=json.loads(source.with_suffix('.mp4.task.json').read_text())
    assert data['requires_source_review'] and data['skip_reason'] == 'below min_source_size_mb'


def test_corrupt_recording_marker_uses_persisted_action_and_recovers(setup):
    wb=setup;source=wb.videos/'session.mp4';source.write_bytes(b'isolated source')
    task=task_id_from_source_relpath('session.mp4');tasks=RecordingTasks(wb)
    first=tasks.queue(task);source.with_suffix('.mp4.pending').write_bytes(b'{broken marker')
    assert tasks.queue(task).job_id == first.job_id
    settings=Settings();pipeline=SimpleNamespace(settings=settings,_save_history=lambda p,d:p.write_text(json.dumps(d)))
    assert tasks.execute(first,pipeline)['skipped']
    assert json.loads(source.with_suffix('.mp4.done').read_text())['state']=='done'
    assert source.read_bytes()==b'isolated source' and not source.with_suffix('.mp4.processing').exists()


def test_corrupt_history_does_not_mask_real_analysis_failure(setup):
    wb=setup;source=wb.videos/'session.mp4';source.write_bytes(b'isolated source')
    tasks=RecordingTasks(wb);job=tasks.queue(task_id_from_source_relpath('session.mp4'))
    broken=b'{unreadable previous history';source.with_suffix('.mp4.task.json').write_bytes(broken)
    settings=Settings();settings.video.min_source_size_mb=0
    def fail(*args):raise RuntimeError('isolated input failure')
    pipeline=SimpleNamespace(settings=settings,run=fail,_save_history=lambda p,d:p.write_text(json.dumps(d)))
    with pytest.raises(RuntimeError,match='isolated input failure'):tasks.execute(job,pipeline)
    data=json.loads(source.with_suffix('.mp4.task.json').read_text())
    assert data['status']=='failed' and data['requires_source_review']
    assert Path(data['prior_history_path']).read_bytes()==broken
    assert source.with_suffix('.mp4.failed').exists() and not source.with_suffix('.mp4.processing').exists()
    assert source.read_bytes()==b'isolated source'


def test_native_client_holds_key_and_uses_native_control_protocol(monkeypatch):
    monkeypatch.setenv('BLREC_API_KEY','isolated-native-key')
    requests=[]
    data=[{'room_info':{'room_id':123,'title':'title','live_status':1,'cover':'https://i0.hdslb.com/bfs/live/cover.jpg'},'user_info':{'name':'name'},
           'task_status':{'running_status':'recording','stream_url':'private-signed-url'},'cookie':'private-cookie'}]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps(data if requests[-1].method=='GET' else {'code':0}).encode()
    def opener(request,timeout):requests.append(request);return Response()
    client=RecorderClient(opener=opener)
    rooms=client.rooms()
    assert rooms[0]['room_info']['cover'] == data[0]['room_info']['cover']
    assert 'private' not in json.dumps(rooms)
    assert requests[-1].get_header('X-api-key') == 'isolated-native-key'
    assert requests[-1].full_url.endswith('/api/v1/tasks/data?page=1&size=20')
    client.control(123,'disable')
    assert requests[-1].full_url.endswith('/123/recorder/disable')
    assert json.loads(requests[-1].data) == {'force':False,'background':True}
    client.control(123,'remove');assert requests[-1].method=='DELETE'
    client.control(123,'monitor_start');assert requests[-1].full_url.endswith('/123/monitor/enable')
    client.control(123,'monitor_stop');assert requests[-1].full_url.endswith('/123/monitor/disable')
    with pytest.raises(ValueError):client.control(123,'unknown')


def test_monitor_only_routes_preserve_auth_and_do_not_enable_recorder():
    from fastapi import Depends, HTTPException, Header
    calls = []
    async def authenticate(x_api_key: str = Header(default='')):
        if x_api_key != 'isolated-key':raise HTTPException(403)
    async def enable(room_id):calls.append(('monitor_enable',room_id))
    async def disable(room_id):calls.append(('monitor_disable',room_id))
    app=FastAPI(dependencies=[Depends(authenticate)])
    app.mount('/',FastAPI())
    install_monitor_controls(app,SimpleNamespace(enable_task_monitor=enable,disable_task_monitor=disable))
    c=TestClient(app)
    assert c.post('/api/v1/tasks/123/monitor/enable').status_code==403
    assert not calls
    for action in ('enable','disable'):
        assert c.post(f'/api/v1/tasks/123/monitor/{action}',headers={'X-API-Key':'isolated-key'}).status_code==200
    assert calls==[('monitor_enable',123),('monitor_disable',123)]


def test_recorder_config_boundary_and_cookie_secrecy(tmp_path,monkeypatch):
    monkeypatch.setenv('BLREC_API_KEY','isolated-key')
    for name in ('BILIVE_VIDEOS_DIR','BILIVE_LOG_DIR'):
        monkeypatch.delenv(name,raising=False)
    config=tmp_path/'settings.toml';config.write_text(Path('settings.example.toml').read_text())
    monkeypatch.setenv('BLREC_CONFIG','before');monkeypatch.setenv('BLREC_OUT_DIR','before')
    monkeypatch.setenv('BLREC_LOG_DIR','before');monkeypatch.setenv('BLREC_PROGRESS','before')
    assert configure(tmp_path) == (tmp_path/'Videos').resolve()
    config.write_text(config.read_text().replace('delete_source = "never"','delete_source = "always"'))
    with pytest.raises(ValueError,match='delete_source'):configure(tmp_path)
    class SettingsStub:
        def dict(self,**kwargs):return {'header':{'cookie':'runtime-secret'}}
    assert persistent_settings_payload(SettingsStub())['header']['cookie'] == ''


def test_workbench_room_api_proxies_native_configuration_only(setup,monkeypatch):
    api=importlib.import_module('bilive.api.app');calls=[]
    monkeypatch.setattr(api,'recorder_client',SimpleNamespace(rooms=lambda page,size:[],
                         control=lambda room,action:calls.append((room,action)) or {'data':{'room_id':room}}))
    client=TestClient(api.app)
    assert client.get('/api/rooms').json()['items'] == []
    assert client.post('/api/rooms/123/remove').status_code == 200
    assert calls == [(123,'remove')]
    assert list(setup.videos.iterdir()) == []


def test_recorder_home_opens_workbench_and_preserves_native_tasks():
    native=FastAPI()
    @native.get('/')
    def old_home():return {'page':'old-home'}
    @native.get('/tasks')
    def tasks():return {'page':'native-recorder'}
    with TestClient(RecorderGateway(native)) as client:
        for method in ('get','head'):
            response=getattr(client,method)('/',follow_redirects=False)
            assert response.status_code==307 and response.headers['location']=='/workbench/'
        assert client.get('/tasks').json()=={'page':'native-recorder'}
        worker=client.get('/ngsw-worker.js')
        assert worker.status_code==200 and worker.headers['cache-control']=='no-store'
        assert "name.startsWith('ngsw:/:')" in worker.text and 'registration.unregister' in worker.text
        assert client.head('/ngsw-worker.js').content==b''
        assert client.get('/ngsw.json').status_code==404
