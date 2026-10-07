from pathlib import Path
from types import SimpleNamespace

import pytest

from bilive.config.settings import UploadConfig
from bilive.db.conn import init_upload_db
from bilive.upload.queue import UploadQueue
from bilive.upload.consumer import UploadConsumer
from bilive.worker.locks import ProcessLock


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "queue.db"
    init_upload_db(db)
    queue = UploadQueue(db)
    video = tmp_path / "final.mp4"
    video.write_bytes(b"isolated fake client media")
    queue.stage(str(video),title="reviewed title")
    counts = {"upload":0,"submit":0}
    def upload(*args):
        counts["upload"] += 1
        return "confirmed-remote"
    def submit(*args):
        counts["submit"] += 1
        return {"code":0,"data":{"bvid":"BVtest-result"}}
    client = SimpleNamespace(check_login=lambda:{"isLogin":True}, upload_file=upload, submit_uploaded_video=submit)
    config = UploadConfig(retry_base=0,delete_after_success=False)
    consumer = UploadConsumer(queue,config,client,tmp_path / "upload.lock")
    return queue,video,client,counts,consumer


def test_staged_never_consumed_then_approved_uploads_and_publishes(setup):
    q,p,c,n,worker = setup
    assert worker.process_one()["status"] == "idle"
    assert n == {"upload":0,"submit":0}
    q.activate(str(p))
    assert worker.process_one()["status"] == "published"
    assert q.get_by_path(str(p))["bvid"] == "BVtest-result"
    assert worker.process_one()["status"] == "idle"
    assert n == {"upload":1,"submit":1}


def test_publication_retry_reuses_remote_and_manual_retry_rejected(setup):
    q,p,c,n,worker = setup
    q.activate(str(p))
    original = c.submit_uploaded_video
    c.submit_uploaded_video = lambda *a: {"code":-1}
    assert worker.process_one()["status"] == "uploaded"
    c.submit_uploaded_video = original
    assert worker.process_one()["status"] == "published"
    assert n["upload"] == 1
    q.mark_failed(str(p), "exhausted")
    with pytest.raises(ValueError):
        q.manual_retry(str(p))


def test_configured_category_reaches_upload_and_publication(setup):
    q,p,c,n,worker = setup
    worker.settings.tid = 19
    metadata = []
    c.upload_file = lambda path,data: metadata.append(dict(data)) or 'confirmed-remote'
    c.submit_uploaded_video = lambda remote,data: metadata.append(dict(data)) or {'code':0,'data':{'bvid':'BVtest'}}
    q.activate(str(p))
    assert worker.process_one()['status'] == 'published'
    assert len(metadata) == 2 and all(v['tid'] == 19 for v in metadata)
    assert all(v['copyright'] == 1 for v in metadata)


def test_segment_category_cover_and_retry_are_preserved(setup):
    q,p,c,n,worker = setup
    cover = p.with_suffix('.jpg')
    cover.write_bytes(b'isolated cover')
    q.stage(str(p), title='title', tid=17, cover_path=str(cover))
    covers = []
    c.upload_cover = lambda path:covers.append(path) or 'https://example.test/cover.jpg'
    metadata = []
    c.submit_uploaded_video = lambda remote,data: metadata.append(dict(data)) or {'code':-1,'message':'isolated failure'}
    q.activate(str(p))
    assert worker.process_one()['status'] == 'uploaded'
    assert worker.process_one()['status'] == 'uploaded'
    assert covers == [str(cover)] and n['upload'] == 1
    assert all(v['tid'] == 17 and v['cover'] == 'https://example.test/cover.jpg' for v in metadata)


def test_authentication_checked_before_claim(setup):
    q,p,c,n,worker = setup
    q.activate(str(p))
    c.check_login = lambda: None
    assert worker.process_one()["status"] == "auth_paused"
    row = q.get_by_path(str(p))
    assert row["stage"] == "queued" and row["attempts"] == 0
    assert n["upload"] == 0


def test_active_consumer_not_recovered_by_another(setup):
    q,p,c,n,worker = setup
    q.activate(str(p))
    q.claim_next()
    with ProcessLock(worker.lock_path):
        assert worker.process_one()["status"] == "busy"
        assert q.get_by_path(str(p))["stage"] == "uploading"
    assert worker.process_one()["status"] == "published"


def test_cleanup_failure_never_reposts(setup, monkeypatch):
    q,p,c,n,worker = setup
    worker.settings.delete_after_success = True
    q.activate(str(p))
    original = Path.unlink
    def fail_cleanup(path, *a, **kw):
        if path == p:
            raise OSError("locked local media")
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,"unlink",fail_cleanup)
    assert worker.process_one()["cleanup_error"]
    assert q.get_by_path(str(p))["stage"] == "published"
    assert worker.process_one()["status"] == "idle"
    assert n["submit"] == 1


def test_submission_error_keeps_code_message_and_redacts_cookie(setup):
    q, p, client, counts, worker = setup
    client.cookies = {'SESSDATA': 'secret-session-value'}
    client.submit_uploaded_video = lambda *args: {'code': 21012, 'message': '转载来源不能为空 secret-session-value'}
    q.activate(str(p))
    out = worker.process_one()
    assert '21012' in out['error'] and '转载来源不能为空' in out['error']
    assert 'secret-session-value' not in out['error']
    row = q.get_by_path(str(p))
    assert row['remote_filename'] == 'confirmed-remote' and row['bvid'] is None
    assert row['last_error'] == out['error'] and counts['upload'] == 1


def test_submission_source_uses_matching_segment_without_reupload(setup, tmp_path):
    import json
    q, p, client, counts, worker = setup
    segments = tmp_path / 'segments'
    segments.mkdir()
    worker.segments_root = segments
    (segments / f'{p.parent.name}.json').write_text(json.dumps({
        'source_relpath': '24393 - 雪狐桑/blive_24393_2026-10-03-142259.mp4', 'final_path': str(p)}), encoding='utf-8')
    q.activate(str(p))
    q.claim_next()
    q.mark_uploaded(str(p), 'existing-remote')
    metadata = []
    client.submit_uploaded_video = lambda remote, data: metadata.append((remote, data.copy())) or {'code': 0, 'data': {'bvid': 'BVtest'}}
    assert worker.process_one()['status'] == 'published'
    assert metadata[0][0] == 'existing-remote'
    assert metadata[0][1]['source'] == 'https://live.bilibili.com/24393'
    assert metadata[0][1]['copyright'] == 1
    assert counts['upload'] == 0


def test_wrong_source_artifact_fails_before_network(setup, tmp_path):
    import json
    q, p, client, counts, worker = setup
    segments = tmp_path / 'segments'
    segments.mkdir()
    worker.segments_root = segments
    (segments / f'{p.parent.name}.json').write_text(json.dumps({'final_path': str(tmp_path / 'other.mp4')}), encoding='utf-8')
    q.activate(str(p))
    assert '不匹配' in worker.process_one()['error']
    assert counts == {'upload': 0, 'submit': 0}
