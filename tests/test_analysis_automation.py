import importlib
from types import SimpleNamespace

from fastapi.testclient import TestClient

from bilive.analysis.tasks import RecordingTasks
from bilive.config.settings import Settings, load_settings, save_analysis_auto_start
from bilive.db.conn import init_upload_db
from bilive.domain.models import ActionJobStatus, task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench
from bilive.upload.queue import UploadQueue


def test_automation_defaults_off_and_persistence_preserves_other_settings(tmp_path):
    config = tmp_path/'settings.toml'
    prior = '# keep\n[worker]\nidle_timeout=42\n[slice.analysis]\npre_judge_asr=false\nauto_start=true\n'
    config.write_text(prior, encoding='utf-8')
    assert Settings().analysis.auto_start is False
    assert load_settings(tmp_path/'missing.toml').analysis.auto_start is False
    for value in (False, True, False):
        assert save_analysis_auto_start(config, value) is value
        loaded = load_settings(config)
        assert loaded.analysis.auto_start is value and not loaded.analysis.pre_judge_asr
        assert loaded.worker.idle_timeout == 42
        assert config.read_text(encoding='utf-8').startswith(prior)
    assert config.read_text(encoding='utf-8').count('[analysis]') == 1


def test_api_saves_without_waking_worker_and_cancels_only_automatic_jobs(tmp_path, monkeypatch):
    config = tmp_path/'config.toml';config.write_text('[analysis]\nauto_start=true\n', encoding='utf-8')
    settings = load_settings(config)
    videos = tmp_path/'Videos';videos.mkdir()
    for rel in ('auto.mp4','manual.mp4'):(videos/rel).write_bytes(b'isolated')
    db = tmp_path/'queue.db';init_upload_db(db)
    jobs = ActionJobStore(tmp_path/'jobs')
    wb = Workbench(videos,tmp_path/'artifacts',jobs,SubtitleStore(tmp_path/'subtitles'),UploadQueue(db))
    automatic = RecordingTasks(wb).queue(task_id_from_source_relpath('auto.mp4'),automatic=True)
    manual = RecordingTasks(wb).queue(task_id_from_source_relpath('manual.mp4'))
    api = importlib.import_module('bilive.api.app')
    for name,value in {'settings':settings,'workbench':wb,'jobs':jobs,
                       'worker_control':SimpleNamespace(wake=lambda:(_ for _ in ()).throw(AssertionError('must not wake')))}.items():
        monkeypatch.setattr(api,name,value)
    client = TestClient(api.app)
    assert client.post('/api/config/analysis-automation',json={'auto_start':'false'}).status_code == 422
    response = client.post('/api/config/analysis-automation',json={'auto_start':False})
    assert response.status_code == 200, response.text
    assert response.json() == {'auto_start':False,'cancelled_pending':1}
    assert load_settings(config).analysis.auto_start is False
    assert [j.job_id for _,j in jobs.valid_jobs(ActionJobStatus.PENDING)] == [manual.job_id]
    assert not (videos/'auto.mp4.pending').exists()
    assert (videos/'manual.mp4.pending').exists()
    assert jobs.load_job(jobs.root/f'{automatic.job_id}.done.json').result['cancelled']
    assert client.post('/api/config/analysis-automation',json={'auto_start':True}).status_code == 200
    assert load_settings(config).analysis.auto_start is True
    assert len(jobs.valid_jobs(ActionJobStatus.PENDING)) == 1
