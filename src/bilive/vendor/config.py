"""Configuration bridge for pinned protocol modules; no old runtime imports."""
import os
from bilive.config.settings import load_settings

settings = load_settings()
MIMO_BASE_URL = settings.mimo.base_url
MIMO_MODEL = settings.mimo.model
MIMO_FPS = settings.mimo.fps
MIMO_MEDIA_RESOLUTION = settings.mimo.media_resolution
MIMO_TIMEOUT = settings.mimo.timeout
MIMO_MAX_BASE64_BYTES = settings.mimo.max_base64_bytes
MIMO_ENCODE_PARALLELISM = int(os.environ.get("BILIVE_MIMO_ENCODE_PARALLELISM", settings.mimo.encode_concurrency))
MIMO_REQUEST_PARALLELISM = settings.mimo.request_concurrency
MIMO_PARALLELISM = MIMO_REQUEST_PARALLELISM
DANMAKU_TIMELINE = True
DANMAKU_MAX_CHARS = settings.analysis.asr_prompt_max_chars
MIN_QUALITY_SCORE = settings.quality.quality
MIN_COMPLETENESS_SCORE = settings.quality.completeness
MIN_CONFIDENCE = settings.quality.confidence
TRIM_ASR_PADDING_SECONDS = settings.subtitle.padding
WHISPER_BATCH_SIZE = int(os.environ.get("BILIVE_WHISPER_BATCH_SIZE", settings.subtitle.asr_batch))
WHISPER_CPU_THREADS = int(os.environ.get("BILIVE_WHISPER_CPU_THREADS", settings.subtitle.asr_cpu_threads))
WHISPER_VAD_FILTER = os.environ.get("BILIVE_WHISPER_VAD_FILTER", str(settings.subtitle.asr_vad)).lower() in {"1", "true", "yes", "on"}
WHISPER_VAD_MIN_SILENCE_MS = settings.multi_modal.whisper_vad_min_silence_ms
WHISPER_VAD_SPEECH_PAD_MS = settings.multi_modal.whisper_vad_speech_pad_ms
