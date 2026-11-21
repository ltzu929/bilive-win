import pytest
import sys
import types

from bilive.vendor.autoslice.mllm_sdk import audio_analyzer


def test_unload_asr_models_clears_cached_models(monkeypatch):
    calls = []
    audio_analyzer._whisper_model = object()
    monkeypatch.setattr(audio_analyzer, "release_gpu_memory", lambda delay=3.0: calls.append(delay))

    audio_analyzer.unload_asr_models()

    assert audio_analyzer._whisper_model is None
    assert calls == [3.0]


def test_transcribe_rejects_removed_asr_engines():
    with pytest.raises(ValueError, match="faster-whisper"):
        audio_analyzer.transcribe_audio_whisper(
            "audio.wav",
            engine="openai-whisper",
        )


def test_unloading_empty_cache_does_not_wait_for_gpu_release(monkeypatch):
    monkeypatch.setattr(audio_analyzer, '_whisper_model', None)
    monkeypatch.setattr(audio_analyzer, '_whisper_model_key', ('stale',))
    calls = []
    monkeypatch.setattr(audio_analyzer, 'release_gpu_memory', lambda: calls.append(True))
    audio_analyzer.unload_asr_models()
    assert not calls and audio_analyzer._whisper_model_key is None


def test_transcription_does_not_accept_silent_partial_batch_result(monkeypatch):
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
            return iter([Segment(), types.SimpleNamespace(
                start=7.46, end=8.66, text='剂量不同，效果不同', words=[])]), types.SimpleNamespace(language="zh")

    class BatchedInferencePipeline:
        def __init__(self, model):
            self.model = model

        def transcribe(self, *args, **kwargs):
            calls.append(("batch", kwargs))
            # The reproduced failure returns a valid first cue without raising.
            return iter([Segment()]), types.SimpleNamespace(language="zh")

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = WhisperModel
    fake_module.BatchedInferencePipeline = BatchedInferencePipeline
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    audio_analyzer._whisper_model = None
    audio_analyzer._whisper_model_key = None

    result = audio_analyzer.transcribe_audio_whisper(
        "audio.wav", cpu_threads=8, batch_size=8, vad_filter=True, hotwords='咩栗, 咩栗羊, 小棉花')

    assert [v['text'] for v in result['segments']] == ['有效字幕', '剂量不同，效果不同']
    assert result["device"] == "cpu"
    assert result["mode"] == "sequential"
    assert calls[0][1]["cpu_threads"] == 8
    assert len(calls) == 2 and calls[1][0] == "sequential"
    assert 'batch_size' not in calls[1][1]
    assert calls[1][1]["vad_parameters"]["min_silence_duration_ms"] == 2000
    assert calls[1][1]["word_timestamps"] is True
    assert 'hotwords' not in calls[1][1]
    assert calls[1][1]['condition_on_previous_text'] is False
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


def test_hotwords_are_not_injected_into_cached_sequential_decoder(monkeypatch):
    calls = []
    loads = []
    cue = types.SimpleNamespace(start=0.1, end=0.9, text='有效字幕', words=[])

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            loads.append(True)

        def transcribe(self, *args, **kwargs):
            calls.append(('sequential', kwargs))
            return iter([cue]), types.SimpleNamespace(language='zh')

    fake = types.ModuleType('faster_whisper')
    fake.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, 'faster_whisper', fake)
    for key in ('_whisper_model', '_whisper_model_key'):
        monkeypatch.setattr(audio_analyzer, key, None)
    for words in ('主播甲, 白露', '主播乙, 白鹿', None):
        calls.clear()
        result = audio_analyzer.transcribe_audio_whisper('audio.wav', hotwords=words)
        assert result['mode'] == 'sequential' and result['segments'][0]['start'] == 0.1
        assert all('hotwords' not in options for _, options in calls)
        assert all(options['word_timestamps'] is True for _, options in calls)
    assert loads == [True]


def test_default_recognition_preserves_speech_inside_vad_gap(monkeypatch):
    from bilive.config.settings import Settings

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            # The retained 2026-10-30 sample loses laughing speech when VAD is on.
            cues = [types.SimpleNamespace(start=10.38, end=13.6, text='哦，看错了', words=[])]
            if not kwargs['vad_filter']:
                cues.append(types.SimpleNamespace(start=32.46, end=35.12,
                                                 text='不好意思，老师，我不是那个意思', words=[]))
            cues.append(types.SimpleNamespace(start=47.53, end=49.73, text='我其实没咋听过', words=[]))
            return iter(cues), types.SimpleNamespace(language='zh')

    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=WhisperModel))
    monkeypatch.setattr(audio_analyzer, '_whisper_model', None)
    monkeypatch.setattr(audio_analyzer, '_whisper_model_key', None)
    result = audio_analyzer.transcribe_audio_whisper('audio.wav', vad_filter=Settings().subtitle.asr_vad)
    assert any('不是那个意思' in cue['text'] for cue in result['segments'])
