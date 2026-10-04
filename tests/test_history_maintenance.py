import json
import subprocess
from pathlib import Path
import pytest
from bilive.db.conn import init_upload_db, open_connection
from bilive.lifecycle.history import inspect_history, main
from bilive.upload.queue import UploadQueue


@pytest.fixture
def historical(tmp_path,monkeypatch):
    videos=tmp_path/'Videos';videos.mkdir();artifacts=tmp_path/'artifacts'
    db=tmp_path/'upload.db';init_upload_db(db)
    monkeypatch.setenv('BILIVE_VIDEOS_DIR',str(videos));monkeypatch.setenv('BILIVE_ARTIFACTS_DIR',str(artifacts))
    monkeypatch.setenv('BILIVE_DB_PATH',str(db));monkeypatch.setenv('BILIVE_RUNTIME_DIR',str(tmp_path/'runtime'))
    monkeypatch.setenv('BILIVE_JOBS_DIR',str(tmp_path/'jobs'))
    source=videos/'session.flv'
    subprocess.run(['ffmpeg','-y','-f','lavfi','-i','testsrc=duration=2:size=160x120:rate=24',
        '-f','lavfi','-i','sine=duration=2','-c:v','libx264','-preset','ultrafast','-c:a','aac','-f','flv',str(source)],check=True,capture_output=True)
    return videos,artifacts,db,tmp_path/'report.json'


def test_default_report_is_read_only_then_explicit_recovery_keeps_queue(historical):
    videos,artifacts,db,report=historical
    original=(videos/'session.flv').read_bytes();database=db.read_bytes()
    assert main(['--report',str(report),'--audit-mp4']) == 0
    assert (videos/'session.flv').read_bytes() == original and not (videos/'session.mp4').exists()
    assert not artifacts.exists() and db.read_bytes() == database
    assert json.loads(report.read_text())['recordings'][0]['status'] == 'would_attempt_recovery'
    assert main(['--report',str(report),'--execute','--audit-mp4']) == 0
    assert not (videos/'session.flv').exists() and (videos/'session.mp4').is_file()
    assert json.loads(report.read_text())['recordings'][0]['status'] == 'recovered'
    assert db.read_bytes() == database


def test_explicit_delete_invalid_does_not_delete_protected_upload_media(historical):
    videos,artifacts,db,report=historical
    invalid=videos/'bad.flv';invalid.write_bytes(b'invalid');target=invalid.with_suffix('.mp4');target.write_bytes(b'invalid target')
    inspect_history(videos,artifacts,db,execute=False,delete_invalid=True,audit_mp4=True)
    assert invalid.exists() and target.exists()
    queue=UploadQueue(db);queue.stage(str(target))
    result=inspect_history(videos,artifacts,db,execute=True,delete_invalid=True,audit_mp4=True)
    assert invalid.exists() and target.exists()
    assert any(item['status'] == 'protected' for item in result['recordings'])
    queue.remove_staged(str(target))
    inspect_history(videos,artifacts,db,execute=True,delete_invalid=True,audit_mp4=True)
    assert not invalid.exists() and not target.exists()


def test_history_execution_rejects_active_queue_and_media_report_destination(historical,monkeypatch):
    videos,artifacts,db,report=historical
    from bilive.jobs.action_jobs import ActionJobStore
    import os
    ActionJobStore(Path(os.environ['BILIVE_JOBS_DIR'])).submit('remux_recording','source',{})
    with pytest.raises(RuntimeError,match='idle durable queue'):main(['--report',str(report),'--execute'])
    assert (videos/'session.flv').exists() and not report.exists()
    with pytest.raises(ValueError,match='report must'):main(['--report',str(videos/'session.json')])
    assert not (videos/'session.json').exists()
