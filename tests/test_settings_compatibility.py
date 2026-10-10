from pathlib import Path
from types import SimpleNamespace
import importlib

from fastapi.testclient import TestClient
from bilive.config.settings import load_settings, Settings


def test_pinned_config_domains_and_explicit_new_keys_take_priority(tmp_path,monkeypatch):
    monkeypatch.delenv('BILIVE_DASHBOARD_ALLOWED_HOSTS',raising=False)
    config=tmp_path/'config.toml'
    config.write_text('''[video]
upload_line="tx"
tid=19
[slice.multi_modal]
whisper_model="local-model"
whisper_device="cpu"
whisper_compute_type="int8"
whisper_batch_size=4
whisper_vad_min_silence_ms=1000
unload_audio_model_after_analysis=false
[slice.subtitle]
font_name="Legacy Font"
font_size=24
outline=2
enable_subtitle_correct=false
asr_batch=2
[slice.analysis]
trim_asr_padding_seconds=8
snap_trim_to_segments=false
pre_judge_asr_max_chars=1234
[slice.mimo]
base_url="https://isolated.example/v1"
[dashboard]
allowed_hosts=["workbench.example"]
''',encoding='utf-8')
    s=load_settings(config)
    assert (s.subtitle.asr_model,s.subtitle.asr_device,s.subtitle.asr_compute_type,s.subtitle.asr_batch) == ('local-model','cpu','int8',2)
    assert s.subtitle.padding == 8 and not s.subtitle.snap_enabled and not s.subtitle.light_correct
    assert s.analysis.asr_prompt_max_chars == 1234 and s.multi_modal.whisper_vad_min_silence_ms == 1000
    assert not s.multi_modal.unload_audio_model_after_analysis
    assert s.subtitle_style()['font_name'] == 'Legacy Font' and s.subtitle_style()['outline'] == 2
    assert s.mimo.base_url == 'https://isolated.example/v1'
    assert s.dashboard.allowed_hosts == ['workbench.example']
    assert s.upload.upload_line == 'tx' and s.upload.tid == 19
    monkeypatch.setenv('BILIVE_WHISPER_BATCH_SIZE','6');monkeypatch.setenv('BILIVE_DASHBOARD_ALLOWED_HOSTS','other.example')
    monkeypatch.setenv('BILIVE_SNAP_TRIM','true')
    overridden=load_settings(config)
    assert overridden.subtitle.asr_batch == 6 and overridden.subtitle.snap_enabled
    assert overridden.dashboard.allowed_hosts == ['other.example']


def test_dashboard_host_allowlist_does_not_authorize_cross_origin_writes(monkeypatch):
    api=importlib.import_module('bilive.api.app');s=Settings();s.dashboard.allowed_hosts=['workbench.example']
    monkeypatch.setattr(api,'settings',s)
    c=TestClient(api.app)
    assert c.get('/api/health',headers={'Host':'workbench.example'}).status_code == 200
    assert c.get('/api/health',headers={'Host':'unapproved.example'}).status_code == 400
    assert c.get('/api/health',headers={'Host':'192.168.1.5'}).status_code == 200
    assert c.post('/api/actions',headers={'Host':'workbench.example','Origin':'http://other.example'},json={}).status_code == 403
    assert c.post('/api/actions',headers={'Host':'workbench.example','Origin':'http://workbench.example'},json={}).status_code == 422


def test_asr_adapter_uses_configured_vad_engine_and_keeps_model_for_task(tmp_path,monkeypatch):
    from bilive.analysis.pipeline import AnalysisPipeline
    from bilive.vendor.autoslice.mllm_sdk import audio_analyzer as audio
    from bilive.worker import preflight
    model=tmp_path/'model';model.mkdir();wave=tmp_path/'audio.wav';wave.write_bytes(b'fixture')
    monkeypatch.setattr(preflight,'cached_asr_model',lambda s:model)
    windows=[]
    monkeypatch.setattr(audio,'extract_audio',lambda path,**kw:windows.append(kw) or str(wave))
    calls=[];unloads=[]
    monkeypatch.setattr(audio,'transcribe_audio_whisper',lambda path,**kw:calls.append(kw) or {'segments':[]})
    monkeypatch.setattr(audio,'unload_asr_models',lambda:unloads.append(True))
    s=Settings();s.multi_modal.whisper_vad_min_silence_ms=750;s.multi_modal.whisper_vad_speech_pad_ms=200
    pipeline=AnalysisPipeline(s,None,None)
    pipeline._transcribe(tmp_path/'source.mp4',hotwords=['白露','匹诺康尼'],start_seconds=2,duration_seconds=3)
    assert calls[0]['engine'] == 'faster-whisper' and calls[0]['model_size'] == str(model)
    assert calls[0]['vad_min_silence_ms'] == 750 and calls[0]['vad_speech_pad_ms'] == 200
    assert calls[0]['hotwords'] == '白露, 匹诺康尼'
    assert windows == [{'start_seconds':2,'duration_seconds':3}]
    assert unloads == [] and not wave.exists()


def test_judgment_and_correction_share_instance_endpoint(monkeypatch):
    from bilive.analysis.pipeline import AnalysisPipeline
    from bilive.vendor.autoslice.mllm_sdk import mimo_video
    from bilive.vendor.autoslice import transcript_correct
    endpoints=[]
    monkeypatch.setattr(mimo_video,'judge_candidate_clips_with_mimo',lambda **kw:endpoints.append((kw['base_url'],kw['model'])) or [])
    monkeypatch.setattr(transcript_correct,'correct_transcript_segments',lambda segments,**kw:endpoints.append((kw['base_url'],kw['model'])) or segments)
    s=Settings();s.mimo.base_url='https://isolated-config.example/v1'
    s.mimo.model='isolated-custom-model'
    pipeline=AnalysisPipeline(s,None,None)
    assert pipeline._judge() == [] and pipeline._correct([]) == []
    assert endpoints == [(s.mimo.base_url,s.mimo.model)] * 2
