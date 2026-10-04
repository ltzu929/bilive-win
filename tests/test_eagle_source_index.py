import importlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from bilive.lifecycle.eagle import source_index
from bilive.media.workbench import Workbench
from bilive.jobs.action_jobs import ActionJobStore
from bilive.upload.queue import UploadQueue
from bilive.media.subtitle_store import SubtitleStore
from bilive.domain.models import task_id_from_source_relpath
from bilive.vendor import streamer_profiles as profiles


def test_eagle_snapshot_reads_only_and_removes_absent_source(tmp_path):
    videos=tmp_path/'Videos';artifacts=tmp_path/'artifacts'
    wb=Workbench(videos,artifacts,ActionJobStore(tmp_path/'jobs'),SubtitleStore(artifacts/'subtitles'),UploadQueue(tmp_path/'absent.db'))
    with pytest.raises(FileNotFoundError):source_index(wb)
    videos.mkdir();room=videos/'123 - 甲';room.mkdir();source=room/'123_20260929-10-00-00.mp4';source.write_bytes(b'fixture')
    before={str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    rows=source_index(wb)
    assert len(rows) == 1 and rows[0]['source_task_id'] == task_id_from_source_relpath('123 - 甲/'+source.name)
    assert rows[0]['recorded_at'] == '2026-09-29 18:00:00'
    assert rows[0]['workspace_url'].startswith('/workbench/?source_task_id=')
    assert rows[0]['source_size_mb'] > 0 and rows[0]['thumbnail_url'] == ''
    assert before == {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert not (tmp_path/'absent.db').exists()
    source.unlink();assert source_index(wb) == []


def test_eagle_api_full_snapshot_and_deep_link_page(tmp_path,monkeypatch):
    api=importlib.import_module('bilive.api.app')
    videos=tmp_path/'Videos';videos.mkdir()
    for i in range(25):(videos/f'session-{i:02d}.mp4').write_bytes(b'fixture')
    monkeypatch.setattr(api.workbench,'videos',videos)
    monkeypatch.setattr(api.settings.paths,'videos_dir',videos)
    monkeypatch.setattr(api,'upload_queue',UploadQueue(tmp_path/'absent.db'))
    with TestClient(api.app) as client:
        response=client.get('/api/eagle/source-recordings',headers={'Origin':'null'})
        assert response.status_code == 200 and len(response.json()) == 25
        assert response.headers['Access-Control-Allow-Origin'] == '*'
        task=task_id_from_source_relpath('session-24.mp4')
        assert client.get('/api/recordings/'+task).json()['page_offset'] == 20
        page=client.get('/api/recordings?offset=20').json()
        assert any(x['task_id'] == task for x in page['items'])
        assert not (tmp_path/'absent.db').exists()
