import pytest
import sys
import types

from bilive.vendor.autoslice.mllm_sdk import audio_analyzer


def test_unload_asr_models_clears_cached_models(monkeypatch):
    calls = []
    audio_analyzer._whisper_model = object()
    audio_analyzer._whisper_batch_pipeline = object()
    monkeypatch.setattr(audio_analyzer, "release_gpu_memory", lambda delay=3.0: calls.append(delay))

    audio_analyzer.unload_asr_models()

    assert audio_analyzer._whisper_model is None
    assert audio_analyzer._whisper_batch_pipeline is None
    assert calls == [3.0]


def test_transcribe_rejects_removed_asr_engines():
    with pytest.raises(ValueError, match="faster-whisper"):
        audio_analyzer.transcribe_audio_whisper(
            "audio.wav",
            engine="openai-whisper",
        )


def test_unloading_empty_cache_does_not_wait_for_gpu_release(monkeypatch):
    monkeypatch.setattr(audio_analyzer, '_whisper_model', None)
    monkeypatch.setattr(audio_analyzer, '_whisper_batch_pipeline', None)
    monkeypatch.setattr(audio_analyzer, '_whisper_model_key', ('stale',))
    calls = []
    monkeypatch.setattr(audio_analyzer, 'release_gpu_memory', lambda: calls.append(True))
    audio_analyzer.unload_asr_models()
    assert not calls and audio_analyzer._whisper_model_key is None


def test_batched_whisper_falls_back_to_sequential(monkeypatch):
    calls = []

    class Segment:
        start = 0.0
        end = 1.5
        text = "有效字幕"

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            calls.append(("model", kwargs))

        def transcribe(self, *args, **kwargs):
            calls.append(("sequential", kwargs))
            return iter([Segment()]), types.SimpleNamespace(language="zh")

    class BatchedInferencePipeline:
        def __init__(self, model):
            self.model = model

        def transcribe(self, *args, **kwargs):
            calls.append(("batch", kwargs))
            raise RuntimeError("batch unavailable")

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = WhisperModel
    fake_module.BatchedInferencePipeline = BatchedInferencePipeline
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    audio_analyzer._whisper_model = None
    audio_analyzer._whisper_batch_pipeline = None
    audio_analyzer._whisper_model_key = None

    result = audio_analyzer.transcribe_audio_whisper("audio.wav", cpu_threads=8, batch_size=8)

    assert result["transcript"] == "有效字幕。"
    assert result["device"] == "cpu"
    assert result["mode"] == "sequential-fallback"
    assert calls[0][1]["cpu_threads"] == 8
    assert calls[1][0] == "batch"
    assert calls[1][1]["batch_size"] == 8
    assert calls[1][1]["vad_parameters"]["min_silence_duration_ms"] == 2000
    assert calls[2][0] == "sequential"
    assert calls[1][1]["word_timestamps"] is True
    assert calls[2][1]["word_timestamps"] is True
    assert result["segments"][0]["words"] == []


def test_transcribe_loads_cuda_model_with_dll_paths(monkeypatch):
    calls = []

    class Segment:
        start = 0.0
        end = 1.0
        text = "上卡"
        words = [types.SimpleNamespace(start=0.1, end=0.9, word="上卡")]

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            calls.append(("model", kwargs))

        def transcribe(self, *args, **kwargs):
            return iter([Segment()]), types.SimpleNamespace(language="zh")

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(
        audio_analyzer,
        "configure_cuda_dll_search_paths",
        lambda: ["C:/nvidia/cublas/bin"],
    )
    audio_analyzer._whisper_model = None
    audio_analyzer._whisper_batch_pipeline = None
    audio_analyzer._whisper_model_key = None

    result = audio_analyzer.transcribe_audio_whisper(
        "audio.wav",
        device="cuda",
        compute_type="float16",
    )

    assert result["device"] == "cuda"
    assert result["compute_type"] == "float16"
    assert result["mode"] == "sequential"
    assert calls[0][1]["device"] == "cuda"
    assert calls[0][1]["compute_type"] == "float16"
    assert result["segments"][0]["words"] == [{"start": 0.1, "end": 0.9, "word": "上卡"}]


@pytest.mark.parametrize('mode', ['batched', 'sequential', 'sequential-fallback'])
def test_hotwords_follow_each_request_in_cached_batch_and_sequential_models(monkeypatch, mode):
    calls = []
    loads = []
    cue = types.SimpleNamespace(start=0.1, end=0.9, text='有效字幕', words=[])

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            loads.append(True)

        def transcribe(self, *args, **kwargs):
            calls.append(('sequential', kwargs))
            return iter([cue]), types.SimpleNamespace(language='zh')

    class BatchedInferencePipeline:
        def __init__(self, model):
            pass

        def transcribe(self, *args, **kwargs):
            calls.append(('batched', kwargs))
            if mode == 'sequential-fallback':
                raise RuntimeError('isolated batch failure')
            return iter([cue]), types.SimpleNamespace(language='zh')

    fake = types.ModuleType('faster_whisper')
    fake.WhisperModel = WhisperModel
    if mode != 'sequential':
        fake.BatchedInferencePipeline = BatchedInferencePipeline
    monkeypatch.setitem(sys.modules, 'faster_whisper', fake)
    for key in ('_whisper_model', '_whisper_batch_pipeline', '_whisper_model_key'):
        monkeypatch.setattr(audio_analyzer, key, None)
    for words in ('主播甲, 白露', '主播乙, 白鹿', None):
        calls.clear()
        result = audio_analyzer.transcribe_audio_whisper('audio.wav', hotwords=words)
        assert result['mode'] == mode and result['segments'][0]['start'] == 0.1
        assert all(options.get('hotwords') == words for _, options in calls)
        assert all(options['word_timestamps'] is True for _, options in calls)
    assert loads == [True]
