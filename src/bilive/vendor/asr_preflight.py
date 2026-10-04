from functools import lru_cache
from bilive.vendor.cuda_runtime import configure_cuda_dll_search_paths

def _configure_cuda_dll_search_paths() -> None:
    configure_cuda_dll_search_paths()

@lru_cache(maxsize=8)
def _check_cuda_asr_runtime(
    model_path: str,
    compute_type: str,
    cpu_threads: int,
) -> tuple[bool, str]:
    """Validate CUDA and faster-whisper once before a worker claims work."""
    _configure_cuda_dll_search_paths()
    try:
        import ctranslate2
    except ImportError:
        return False, "ctranslate2 is not installed for CUDA ASR"

    try:
        device_count = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:
        return False, f"CUDA runtime is unavailable: {exc}"
    if device_count < 1:
        return False, "CUDA ASR requires at least one visible device"

    try:
        supported = ctranslate2.get_supported_compute_types("cuda")
    except Exception as exc:
        return False, f"CUDA compute types are unavailable: {exc}"
    if compute_type not in supported:
        return False, (
            f"CUDA compute type is unsupported: {compute_type}; "
            f"available={sorted(str(item) for item in supported)}"
        )

    try:
        from faster_whisper import WhisperModel
        import numpy as np

        model = WhisperModel(
            model_path,
            device="cuda",
            compute_type=compute_type,
            cpu_threads=max(1, int(cpu_threads)),
        )
        probe_audio = np.zeros(16000, dtype=np.float32)
        probe_segments, _probe_info = model.transcribe(
            probe_audio,
            language="zh",
            vad_filter=False,
            without_timestamps=True,
            beam_size=1,
            best_of=1,
            # A readiness check needs one decoder step, not a full silence
            # transcription that can hallucinate hundreds of output tokens.
            max_new_tokens=1,
        )
        list(probe_segments)
        del model
    except Exception as exc:
        return False, f"faster-whisper CUDA probe failed: {exc}"
    return True, (
        f"CUDA ASR ready: device_count={device_count}, "
        f"compute_type={compute_type}"
    )

