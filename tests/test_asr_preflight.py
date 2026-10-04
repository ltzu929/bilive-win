from pathlib import Path
from types import SimpleNamespace
import sys
from importlib.machinery import ModuleSpec
from bilive.config.settings import Settings
from bilive.jobs.action_jobs import ActionJobStore
from bilive.worker.preflight import check_action
from bilive.vendor.asr_preflight import _check_cuda_asr_runtime


def test_cuda_failure_stays_pending_and_can_recover(tmp_path,monkeypatch):
    import bilive.vendor.asr_preflight as runtime
    from bilive.domain.models import task_id_from_source_relpath
    jobs=ActionJobStore(tmp_path/'jobs');job=jobs.submit('start_analysis',task_id_from_source_relpath('session.mp4'),{})
    settings=Settings();model=tmp_path/'model';model.mkdir();settings.subtitle.asr_model=str(model)
    videos=tmp_path/'Videos';videos.mkdir();(videos/'session.mp4').write_bytes(b'fixture')
    settings.paths.videos_dir=videos;settings.video.min_source_size_mb=0
    monkeypatch.setenv('MIMO_API_KEY','isolated-preflight-key')
    monkeypatch.setattr(runtime,'_configure_cuda_dll_search_paths',lambda:None)
    ct=SimpleNamespace(get_cuda_device_count=lambda:1,get_supported_compute_types=lambda device:{'float16'})
    monkeypatch.setitem(sys.modules,'ctranslate2',ct)
    class Model:
        def __init__(self,*args,**kwargs):raise RuntimeError('missing cuDNN isolated-preflight-key')
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=Model,__spec__=ModuleSpec('faster_whisper',loader=None)))
    _check_cuda_asr_runtime.cache_clear()
    check=lambda j:check_action(j,settings)
    assert jobs.claim('owner',check) is None
    pending=jobs.load_job(jobs.root/f'{job.job_id}.pending.json')
    assert 'probe failed' in pending.progress['reason'] and 'isolated-preflight-key' not in pending.progress['reason']
    assert not (tmp_path/'queue.db').exists()
    class Ready:
        def __init__(self,*args,**kwargs):assert kwargs['device'] == 'cuda' and kwargs['compute_type'] == 'float16'
        def transcribe(self,*args,**kwargs):
            assert kwargs['max_new_tokens'] == 1
            return iter([]),None
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=Ready,__spec__=ModuleSpec('faster_whisper',loader=None)))
    assert jobs.claim('owner',check).job_id == job.job_id
    _check_cuda_asr_runtime.cache_clear()


def test_cuda_visibility_and_compute_type_are_required(tmp_path,monkeypatch):
    import bilive.vendor.asr_preflight as runtime
    monkeypatch.setattr(runtime,'_configure_cuda_dll_search_paths',lambda:None)
    ct=SimpleNamespace(get_cuda_device_count=lambda:0,get_supported_compute_types=lambda device:{'int8'})
    monkeypatch.setitem(sys.modules,'ctranslate2',ct);_check_cuda_asr_runtime.cache_clear()
    ok,reason=_check_cuda_asr_runtime(str(tmp_path),'float16',8)
    assert not ok and 'visible device' in reason
    ct.get_cuda_device_count=lambda:1;_check_cuda_asr_runtime.cache_clear()
    ok,reason=_check_cuda_asr_runtime(str(tmp_path),'float16',8)
    assert not ok and 'unsupported' in reason
    _check_cuda_asr_runtime.cache_clear()

def test_asr_snapshot_does_not_load_model_or_write_database(tmp_path,monkeypatch):
    from bilive.worker import preflight
    settings=Settings();settings.paths.db_path=tmp_path/'missing.db'
    monkeypatch.setattr(preflight,'cached_asr_model',lambda config:(_ for _ in ()).throw(FileNotFoundError('not cached')))
    monkeypatch.setitem(sys.modules,'ctranslate2',SimpleNamespace(get_cuda_device_count=lambda:1,get_supported_compute_types=lambda device:{'float16'}))
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('read loaded model'))))
    data=preflight.asr_dependency_snapshot(settings)
    assert data['device_count'] == 1 and data['supported_compute_types'] == ['float16']
    assert not data['model_cached'] and not data['inference_verified'] and not settings.paths.db_path.exists()


def test_local_transcription_requires_local_asr_but_no_mimo(tmp_path, monkeypatch):
    import bilive.worker.preflight as preflight
    monkeypatch.delenv('MIMO_API_KEY', raising=False)
    settings = Settings()
    settings.subtitle.asr_device = 'cpu'
    settings.subtitle.asr_compute_type = 'int8'
    monkeypatch.setattr(preflight.shutil, 'which', lambda name: name)
    monkeypatch.setattr(preflight.importlib.util, 'find_spec', lambda name: object() if name == 'faster_whisper' else None)
    monkeypatch.setattr(preflight, 'cached_asr_model', lambda settings: tmp_path)
    job = SimpleNamespace(action='transcribe_subtitles')
    assert check_action(job, settings) == (True, '')
    monkeypatch.setattr(preflight, 'cached_asr_model', lambda settings: (_ for _ in ()).throw(FileNotFoundError()))
    assert check_action(job, settings) == (False, 'ASR model is not cached locally')
