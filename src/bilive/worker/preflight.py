"""Read-only action dependency checks. No downloads or DB initialization."""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path


def cached_asr_model(settings) -> Path:
    model = settings.subtitle.asr_model
    path = Path(model)
    if path.is_dir():
        return path.resolve()
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(repo_id=model if '/' in model else f'Systran/faster-whisper-{model}',
                                  local_files_only=True)).resolve()


def asr_dependency_snapshot(settings):
    data = {'device':settings.subtitle.asr_device,'compute_type':settings.subtitle.asr_compute_type,
            'model':settings.subtitle.asr_model,'model_cached':False,'device_count':None,
            'supported_compute_types':[], 'inference_verified':False}
    try:
        cached_asr_model(settings)
        data['model_cached'] = True
    except Exception:
        data['model_detail'] = '本地模型不可用'
    try:
        from bilive.vendor.cuda_runtime import configure_cuda_dll_search_paths
        configure_cuda_dll_search_paths()
        import ctranslate2
        device = str(settings.subtitle.asr_device).lower()
        data['device_count'] = int(ctranslate2.get_cuda_device_count()) if device == 'cuda' else None
        data['supported_compute_types'] = sorted(ctranslate2.get_supported_compute_types(device))
    except Exception as exc:
        data['runtime_detail'] = type(exc).__name__
    return data


def check_action(job, settings=None) -> tuple[bool, str]:
    if job.action == 'start_analysis' and settings is not None:
        from bilive.domain.models import source_relpath_from_task_id
        source = (settings.videos_root() / source_relpath_from_task_id(job.target)).resolve()
        if (source.is_relative_to(settings.videos_root()) and source.is_file()
                and source.stat().st_size < settings.video.min_source_size_mb * 1048576):
            return True, ''  # Mark skipped without pretending analysis/review succeeded.
    if job.action in {"finalize", "render", "reburn", "remux_recording", "start_analysis", "retry_judge", "transcribe_subtitles"}:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            return False, "FFmpeg / ffprobe unavailable"
    if job.action in {"start_analysis", "retry_judge"}:
        if not os.environ.get("MIMO_API_KEY"):
            return False, "MiMo API key is not configured"
        if importlib.util.find_spec("openai") is None:
            return False, "MiMo client dependency unavailable"
        if job.action == 'retry_judge':
            return True, ''  # Auxiliary ASR cannot block a new judgment.
    if job.action in {'start_analysis', 'transcribe_subtitles'}:
        if settings is not None and settings.multi_modal.whisper_engine != 'faster-whisper':
            return False,'Only faster-whisper is supported'
        if importlib.util.find_spec("faster_whisper") is None:
            return False, "faster-whisper dependency unavailable"
        if settings is not None:
            try:
                model_path = cached_asr_model(settings)
            except Exception:
                return False, 'ASR model is not cached locally'
            device = str(settings.subtitle.asr_device).lower()
            if device not in {'cpu','cuda'}:
                return False, 'unsupported ASR device'
            if device == 'cuda':
                from bilive.vendor.asr_preflight import _check_cuda_asr_runtime
                ok, reason = _check_cuda_asr_runtime(str(model_path.resolve()),
                    settings.subtitle.asr_compute_type,max(1,int(settings.subtitle.asr_cpu_threads)))
                if not ok:
                    # Dependency fixes must become visible without a process restart.
                    _check_cuda_asr_runtime.cache_clear()
                    from bilive.config.secrets import redact
                    return False, redact(reason)
    return True, ""
