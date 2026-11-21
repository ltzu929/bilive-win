"""Compare post-ASR name correction with saved human edits; never overwrite clips."""
from dataclasses import asdict
import json
import logging
from pathlib import Path
import sys
import time

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
sys.stdout.reconfigure(encoding='utf-8')
from bilive.config.secrets import load_secret_env
from bilive.config.settings import load_settings
from bilive.lifecycle.profiles import profile_for_source, subtitle_hotwords_for_profile
from bilive.vendor.autoslice.analysis_result import TranscriptSegment
from bilive.vendor.autoslice.transcript_correct import correct_transcript_segments

logging.basicConfig(level=logging.INFO)
load_secret_env(root / '.secrets/env')
settings = load_settings()
sid = 'MjIzODQ1MTYgLSDlkZznsbMvYmxpdmVfMjIzODQ1MTZfMjAyNi0xMC0zMC0yMDU2NDcubXA0fDE1NzIwMDAtMTcxMzAwMA'
segment = json.loads((root / 'artifacts/segments' / f'{sid}.json').read_text(encoding='utf-8'))
original = json.loads((root / 'artifacts' / sid / 'analysis_sidecar.json').read_text(encoding='utf-8'))['transcript_segments']
human = json.loads((root / 'artifacts/subtitles' / f'{sid}.json').read_text(encoding='utf-8'))['lines']
lines = [TranscriptSegment(v['start'], v['end'], v['text']) for v in original]
profile = profile_for_source(root / 'Videos', segment['source_relpath'])
names = subtitle_hotwords_for_profile(profile)
out = Path(__file__).resolve().parent / 'name-correction'
out.mkdir(exist_ok=True)
results = []
for name, words in [('current', names), ('with_confirmed_name', list(dict.fromkeys([*names, '咩栗'])))]:
    started = time.monotonic()
    corrected = correct_transcript_segments(lines, artist=profile['display_name'],
        title=segment['title'], hotwords=words, model=settings.mimo.model,
        base_url=settings.mimo.base_url, timeout=settings.mimo.timeout)
    assert len(corrected) == len(lines)
    assert [(v.start, v.end) for v in corrected] == [(v.start, v.end) for v in lines]
    changes = [{'index': i, 'before': a.text, 'after': b.text, 'human': human[i]['text']}
               for i, (a, b) in enumerate(zip(lines, corrected)) if a.text != b.text]
    targets = [i for i, (a, h) in enumerate(zip(lines, human)) if a.text != h['text'] and '咩栗' in h['text']]
    hits = sum(corrected[i].text == human[i]['text'] for i in targets)
    result = {'case': name, 'hotwords': words, 'lines': len(lines),
              'elapsed_seconds': round(time.monotonic()-started, 2), 'name_targets': targets,
              'exact_name_matches': hits, 'changes': changes,
              'segments': [asdict(v) for v in corrected]}
    results.append(result)
    (out / 'comparison.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'segments'}, ensure_ascii=False), flush=True)
