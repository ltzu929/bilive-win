"""Compare decoding options on retained candidate audio without changing drafts."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from bilive.config.settings import load_settings
from bilive.lifecycle.profiles import profile_for_source, subtitle_hotwords_for_profile
from bilive.vendor.cuda_runtime import configure_cuda_dll_search_paths
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps

parser = argparse.ArgumentParser()
parser.add_argument("--source", default="8792912 - 咩栗/blive_8792912_2026-10-30-185558.mp4")
parser.add_argument("--start", type=float, default=1338)
parser.add_argument("--end", type=float, default=1578)
parser.add_argument("--name", default="today")
parser.add_argument("--from-source", action="store_true")
parser.add_argument("--cases", nargs="+", default=["baseline", "no_hotwords"])
args = parser.parse_args()
out = Path(__file__).resolve().parent / args.name
out.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO)
logging.getLogger("faster_whisper").setLevel(logging.DEBUG)
settings = load_settings(ROOT / ".secrets/runtime.toml")
vad = {"min_silence_duration_ms": settings.multi_modal.whisper_vad_min_silence_ms,
       "speech_pad_ms": settings.multi_modal.whisper_vad_speech_pad_ms}
audio_path = out / "audio.wav"
if not audio_path.exists():
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if args.from_source:
        command += ["-ss", str(args.start), "-i", str(ROOT / "Videos" / args.source),
                    "-t", str(args.end - args.start)]
    else:
        task = json.loads((ROOT / "Videos" / (args.source + ".task.json")).read_text(encoding="utf-8"))
        candidate = next(c for c in task["candidates"] if c["start"] == args.start and c["end"] == args.end)
        command += ["-i", candidate["path"]]
    subprocess.run(command + [
                    "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(audio_path)], check=True)
audio = decode_audio(str(audio_path))
speech = get_speech_timestamps(audio, VadOptions(**vad))
(out / "vad.json").write_text(json.dumps(
    [{k: v / 16000 for k, v in region.items()} for region in speech], indent=2), encoding="utf-8")
hotwords = ", ".join(subtitle_hotwords_for_profile(profile_for_source(ROOT / "Videos", args.source)))
configure_cuda_dll_search_paths()
model = WhisperModel(settings.subtitle.asr_model, device=settings.subtitle.asr_device,
                     compute_type=settings.subtitle.asr_compute_type,
                     cpu_threads=settings.subtitle.asr_cpu_threads, local_files_only=True)
trace = []
original_generate = model.generate_with_fallback


def traced_generate(encoder_output, prompt, tokenizer, options):
    result = original_generate(encoder_output, prompt, tokenizer, options)
    generated, logprob, temperature, ratio = result
    trace.append({"prompt": tokenizer.decode(prompt), "text": tokenizer.decode(generated.sequences_ids[0]),
                  "avg_logprob": logprob, "temperature": temperature, "compression_ratio": ratio,
                  "no_speech_prob": generated.no_speech_prob})
    return result


model.generate_with_fallback = traced_generate
cases = {"baseline": {"hotwords": hotwords}, "no_hotwords": {},
         "reset_context": {"hotwords": hotwords, "condition_on_previous_text": False},
         "no_hotwords_reset_context": {"condition_on_previous_text": False},
         "no_vad": {"hotwords": hotwords, "vad_filter": False},
         "no_vad_no_hotwords": {"vad_filter": False, "condition_on_previous_text": False},
         "no_silence_skip": {"vad_filter": False, "condition_on_previous_text": False,
                             "no_speech_threshold": None}}
for name in args.cases:
    trace.clear()
    started = time.monotonic()
    options = {"language": "zh", "vad_filter": settings.subtitle.asr_vad,
               "vad_parameters": vad.copy(), "without_timestamps": False,
               "word_timestamps": True, **cases[name]}
    iterator, info = model.transcribe(audio, **options)
    segments = [asdict(v) for v in iterator]
    elapsed = time.monotonic() - started
    payload = {"sample": args.name, "source": args.source, "origin": args.start,
               "options": cases[name], "elapsed_seconds": round(elapsed, 2),
               "info": asdict(info), "segments": segments, "decoder": trace.copy()}
    (out / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"sample": args.name, "case": name, "elapsed_seconds": round(elapsed, 2),
                      "lines": len(segments)}, ensure_ascii=False), flush=True)
