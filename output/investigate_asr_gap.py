import sys, json, logging, subprocess, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bilive.config.settings import load_settings
from bilive.vendor.cuda_runtime import configure_cuda_dll_search_paths
from bilive.vendor.autoslice.mllm_sdk.audio_analyzer import extract_audio
from bilive.lifecycle.profiles import profile_for_source, subtitle_hotwords_for_profile
from faster_whisper import WhisperModel, BatchedInferencePipeline
from faster_whisper.audio import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps, collect_chunks
logging.basicConfig(level=logging.INFO)
root = Path(__file__).resolve().parents[1]
out = root / 'output/asr-gap-20261016'
out.mkdir(exist_ok=True)
tempfile.tempdir = str(out)
s = load_settings(root / '.secrets/runtime.toml')
rel = '8792912 - 咩栗/blive_8792912_2026-10-16-185650.mp4'
audio_path = extract_audio(str(root / 'Videos' / rel), start_seconds=5509, duration_seconds=135)
audio = decode_audio(audio_path, sampling_rate=16000)
Path(audio_path).replace(out / 'audio.wav')
vad = dict(min_silence_duration_ms=s.multi_modal.whisper_vad_min_silence_ms, speech_pad_ms=s.multi_modal.whisper_vad_speech_pad_ms)
clips = get_speech_timestamps(audio, VadOptions(**vad, max_speech_duration_s=30))
chunks, metadata = collect_chunks(audio, clips, max_duration=30)
result = {'audio_samples':len(audio), 'vad':[{k:v/16000 for k,v in c.items()} for c in clips], 'chunks':metadata,
          'config':{'model':s.subtitle.asr_model, 'device':s.subtitle.asr_device, 'compute_type':s.subtitle.asr_compute_type, 'batch':s.subtitle.asr_batch, 'vad':vad}, 'runs':{}}
(out / 'diagnostic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'duration':len(audio)/16000,'vad':result['vad']},ensure_ascii=False),flush=True)
configure_cuda_dll_search_paths()
model = WhisperModel(s.subtitle.asr_model,device=s.subtitle.asr_device,compute_type=s.subtitle.asr_compute_type,cpu_threads=s.subtitle.asr_cpu_threads,local_files_only=True)
hotwords = ', '.join(subtitle_hotwords_for_profile(profile_for_source(root/'Videos', rel))) or None
options = dict(language='zh', vad_filter=s.subtitle.asr_vad, vad_parameters=vad.copy(), without_timestamps=False, word_timestamps=True,hotwords=hotwords)
for name,engine,extra in [('batched',BatchedInferencePipeline(model),{'batch_size':s.subtitle.asr_batch}),('sequential',model,{})]:
    iterator,info = engine.transcribe(audio,**options,**extra)
    result['runs'][name] = [v._asdict() for v in iterator]
    (out / 'diagnostic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(name,json.dumps([{'start':v['start'],'end':v['end'],'text':v['text']} for v in result['runs'][name]],ensure_ascii=False),flush=True)
