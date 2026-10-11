import importlib
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bilive.config.settings import Settings
from bilive.db.conn import init_upload_db
from bilive.jobs.action_jobs import ActionJobStore
from bilive.upload.queue import UploadQueue
from bilive.worker.control import WorkerControl
from bilive.worker.runtime import ConsumerProcess, create_worker_app
from bilive.worker.runner import Worker


def test_dashboard_rejects_foreign_origin_before_runtime_action(monkeypatch, tmp_path):
    api = importlib.import_module('bilive.api.app')
    calls = []
    monkeypatch.setattr(api, 'worker_control', SimpleNamespace(runtime=tmp_path,
        wake=lambda:calls.append('wake') or {'state':'starting'}))
    client = TestClient(api.app)
    assert client.post('/api/worker/wake', headers={'Origin':'https://foreign.invalid'}).status_code == 403
    assert not calls
    assert client.post('/api/worker/wake', headers={'Origin':'http://testserver'}).status_code == 200
    assert calls == ['wake']


def test_user_pause_blocks_automatic_worker_wake(tmp_path, monkeypatch):
    control = WorkerControl(tmp_path, tmp_path)
    (tmp_path / 'worker-paused').write_text('user pause')
    monkeypatch.setattr(control, 'status', lambda: pytest.fail('paused wake must not contact runtime'))
    assert control.wake()['state'] == 'paused'


def test_dashboard_stop_persists_pause_until_explicit_wake(tmp_path, monkeypatch):
    api = importlib.import_module('bilive.api.app')
    control = WorkerControl(tmp_path, tmp_path / 'runtime', port=1)
    monkeypatch.setattr(api, 'worker_control', control)
    client = TestClient(api.app)
    assert client.post('/api/worker/stop').json()['state'] == 'paused'
    assert (control.runtime / 'worker-paused').exists()
    assert control.wake()['state'] == 'paused'
    monkeypatch.setattr(control, 'wake', lambda: {'state':'starting'})
    assert client.post('/api/worker/wake').json()['state'] == 'starting'
    assert not (control.runtime / 'worker-paused').exists()


def test_config_diagnostics_are_read_only_and_do_not_expose_credentials(tmp_path, monkeypatch):
    api = importlib.import_module('bilive.api.app')
    settings = Settings()
    settings.paths.videos_dir = tmp_path / 'absent-videos'
    settings.paths.db_path = tmp_path / 'absent.db'
    settings.paths.cookie_file = tmp_path / 'absent-cookie'
    monkeypatch.setattr(api, 'settings', settings)
    monkeypatch.setenv('MIMO_API_KEY', 'isolated-secret-never-return')
    response = TestClient(api.app).get('/api/config/summary')
    assert response.status_code == 200
    assert 'isolated-secret-never-return' not in response.text
    assert response.json()['diagnostics']['mimo_key_configured']
    assert not response.json()['diagnostics']['upload_database_available']
    assert list(tmp_path.iterdir()) == []


def test_api_task_survives_failed_wake(tmp_path,monkeypatch):
    api = importlib.import_module("bilive.api.app")
    jobs = ActionJobStore(tmp_path / "jobs")
    monkeypatch.setattr(api,"jobs",jobs)
    def fail():
        raise OSError("isolated launch failure")
    monkeypatch.setattr(api,"worker_control",SimpleNamespace(wake=fail))
    db = tmp_path / "queue.db"
    init_upload_db(db)
    queue = UploadQueue(db)
    queue.stage("reviewed.mp4",title="reviewed")
    queue.mark_failed("reviewed.mp4","upload failed")
    monkeypatch.setattr(api,"upload_queue",queue)
    r = TestClient(api.app).post("/api/uploads/retry",json={"video_path":"reviewed.mp4","stage":"staged"})
    assert r.status_code == 200
    assert r.json()["worker"]["state"] == "unavailable"
    assert (jobs.root / f"{r.json()['job_id']}.pending.json").is_file()


def test_retry_uses_database_state_instead_of_claimed_client_stage(tmp_path,monkeypatch):
    api = importlib.import_module("bilive.api.app")
    db = tmp_path / "queue.db"
    init_upload_db(db)
    q = UploadQueue(db)
    q.stage("protected.mp4",title="title")
    q.activate("protected.mp4")
    q.mark_uploaded("protected.mp4","remote")
    q.mark_failed("protected.mp4","failed after CDN")
    monkeypatch.setattr(api,"upload_queue",q)
    r = TestClient(api.app).post("/api/uploads/retry",json={"video_path":"protected.mp4","stage":"failed","remote_filename":None})
    assert r.status_code == 422


def test_worker_stop_is_graceful_and_external_origin_denied(tmp_path):
    worker = Worker(ActionJobStore(tmp_path / "jobs"),None,tmp_path / "worker.lock")
    events = []
    consumer = SimpleNamespace(status=lambda:{"state":"offline"},start=lambda:{"state":"running"},stop=lambda:events.append("stop"))
    c = TestClient(create_worker_app(worker,consumer))
    assert c.post("/stop",headers={"Origin":"https://untrusted.example"}).status_code == 403
    assert not worker.stop_event.is_set()
    assert c.post("/stop").json()["accepted"]
    assert worker.stop_event.is_set() and events == ["stop"]


def test_consumer_process_is_owned_and_does_not_respawn_failed_child(tmp_path,monkeypatch):
    settings = Settings()
    cookie = tmp_path / "cookie"
    cookie.write_text("not real credentials")
    settings.paths.cookie_file = cookie
    db = tmp_path / "queue.db"
    init_upload_db(db)
    q = UploadQueue(db)
    q.stage("reviewed.mp4",title="title")
    q.activate("reviewed.mp4")
    exit_code = [None]
    child = SimpleNamespace(poll=lambda:exit_code[0])
    calls = []
    def popen(*a,**kw):
        calls.append((a,kw));return child
    monkeypatch.setenv("BILIVE_AUTO_UPLOAD","1")
    monkeypatch.setattr("bilive.worker.runtime.importlib.util.find_spec",lambda name:object())
    process = ConsumerProcess(settings,db,tmp_path / "runtime",tmp_path,popen=popen)
    process.tick()
    assert len(calls) == 1 and process.status()["state"] == "running"
    process.tick()
    assert len(calls) == 1
    exit_code[0] = 1
    process.tick();process.tick()
    assert len(calls) == 1 and process.status()["last_exit"] == 1


def test_read_only_status_does_not_start_or_create_paths(tmp_path):
    control = WorkerControl(tmp_path,tmp_path / "runtime",port=1)
    assert control.status()["state"] == "offline"
    assert not (tmp_path / "runtime").exists()


def test_actual_consumer_stop_finishes_inflight_work_without_claiming_next(tmp_path):
    runtime=tmp_path/'runtime';runtime.mkdir()
    db=tmp_path/'queue.db';init_upload_db(db);queue=UploadQueue(db)
    videos=[tmp_path/f'final-{i}.mp4' for i in range(2)]
    for p in videos:
        p.write_bytes(b'isolated mock network media');queue.stage(str(p),title='approved');queue.activate(str(p))
    project=Path(__file__).resolve().parents[1]
    env=os.environ.copy();env['PYTHONPATH']=str(project/'src')
    process=subprocess.Popen([sys.executable,str(project/'tests/fixtures/blocking_upload_consumer.py'),str(db),str(runtime)],
        cwd=project,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    controller=ConsumerProcess(Settings(),db,runtime,project);controller.process=process
    try:
        deadline=time.monotonic()+10
        while not (runtime/'network-started').exists():
            assert process.poll() is None
            assert time.monotonic()<deadline
            time.sleep(.02)
        controller.stop()
        assert process.poll() is None and queue.get_by_path(str(videos[0]))['stage']=='uploading'
        assert queue.get_by_path(str(videos[1]))['stage']=='queued'
        (runtime/'network-release').touch()
        process.wait(timeout=10)
        assert process.returncode==0,process.stderr.read().decode(errors='replace')
        assert queue.get_by_path(str(videos[0]))['stage']=='published'
        assert queue.get_by_path(str(videos[1]))['stage']=='queued'
        controller.tick()
        assert controller.process is None and not controller.enabled
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=5)


def test_actual_loopback_worker_start_health_stop(tmp_path):
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        port = sock.getsockname()[1]
    project = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(PYTHONPATH=str(project / "src"),BILIVE_VIDEOS_DIR=str(tmp_path / "Videos"),
               BILIVE_DB_PATH=str(tmp_path / "queue.db"),BILIVE_ARTIFACTS_DIR=str(tmp_path / "artifacts"),
               BILIVE_JOBS_DIR=str(tmp_path / "jobs"),BILIVE_RUNTIME_DIR=str(tmp_path / "runtime"),
               BILIVE_AUTO_UPLOAD="0",BILIVE_WORKER_IDLE_TIMEOUT="30")
    process = subprocess.Popen([sys.executable,str(project / "scripts/start_worker.py"),"--serve","--port",str(port)],
        cwd=project,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    control = WorkerControl(project,tmp_path / "runtime",port)
    try:
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            if process.poll() is not None:
                pytest.fail(process.stderr.read().decode(errors="replace"))
            if control.status().get("service") == "bilive-worker":
                break
            time.sleep(0.1)
        else:
            pytest.fail("isolated Worker did not become ready")
        assert (tmp_path / "queue.db").is_file()
        assert control.stop()["accepted"]
        process.wait(timeout=10)
        assert process.returncode == 0
        assert control.status()["state"] == "offline"
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=10)
