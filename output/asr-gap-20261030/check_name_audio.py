"""Compare short audio rechecks around human-corrected names."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
sys.stdout.reconfigure(encoding='utf-8')
from bilive.config.secrets import load_secret_env
from bilive.config.settings import load_settings
from bilive.vendor.autoslice.mllm_sdk.audio_analyzer import extract_audio
from bilive.vendor.cuda_runtime import configure_cuda_dll_search_paths
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

load_secret_env(root / '.secrets/env')
settings = load_settings()
sid = 'MjIzODQ1MTYgLSDlkZznsbMvYmxpdmVfMjIzODQ1MTZfMjAyNi0xMC0zMC0yMDU2NDcubXA0fDE1NzIwMDAtMTcxMzAwMA'
segment = json.loads((root / 'artifacts/segments' / f'{sid}.json').read_text(encoding='utf-8'))
original = json.loads((root / 'artifacts' / sid / 'analysis_sidecar.json').read_text(encoding='utf-8'))['transcript_segments']
human = json.loads((root / 'artifacts/subtitles' / f'{sid}.json').read_text(encoding='utf-8'))['lines']
out = Path(__file__).resolve().parent / 'name-correction'
tempfile.tempdir = str(out)
audio_path = out / 'segment.wav'
if not audio_path.exists():
    extracted = extract_audio(str(root / 'Videos' / segment['source_relpath']),
        start_seconds=segment['source_start'],
        duration_seconds=segment['source_end']-segment['source_start'])
    Path(extracted).replace(audio_path)
audio = decode_audio(str(audio_path))
configure_cuda_dll_search_paths()
model = WhisperModel(settings.subtitle.asr_model, device=settings.subtitle.asr_device,
                     compute_type=settings.subtitle.asr_compute_type,
                     cpu_threads=settings.subtitle.asr_cpu_threads, local_files_only=True)
results = []
for i, (line, corrected) in enumerate(zip(original, human)):
    if line['text'] == corrected['text'] or '咩栗' not in corrected['text']:
        continue
    start = max(0, line['start']-2)
    end = min(len(audio)/16000, line['end']+2)
    piece = audio[int(start*16000):int(end*16000)]
    record = {'index':i, 'before':line['text'], 'human':corrected['text'], 'runs':{}}
    for case, words in [('unhinted', None), ('hinted', '呜米, 呜米喵, 小米星, 咩栗')]:
        iterator, info = model.transcribe(piece, language='zh', vad_filter=False,
            condition_on_previous_text=False, word_timestamps=True, hotwords=words)
        record['runs'][case] = [asdict(v) for v in iterator]
    results.append(record)
    (out / 'audio_rechecks.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**{k:v for k,v in record.items() if k!='runs'},
                      'runs':{k:' '.join(v['text'] for v in rows) for k,rows in record['runs'].items()}},
                     ensure_ascii=False), flush=True)
