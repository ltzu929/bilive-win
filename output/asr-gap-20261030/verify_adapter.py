"""Exercise the patched production adapter on recent retained recordings."""
import json
import logging
from pathlib import Path
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
sys.stdout.reconfigure(encoding='utf-8')
from bilive.config.settings import load_settings
from bilive.analysis.pipeline import AnalysisPipeline
from bilive.lifecycle.profiles import profile_for_source, subtitle_hotwords_for_profile
from bilive.vendor.autoslice.mllm_sdk.audio_analyzer import unload_asr_models

out = Path(__file__).resolve().parent
tempfile.tempdir = str(out)
logging.basicConfig(level=logging.INFO)
settings = load_settings(root / '.secrets/runtime.toml')
assert settings.subtitle.asr_vad is False
pipeline = AnalysisPipeline(settings, None, None)
cases = [
    ('today', '8792912 - 咩栗/blive_8792912_2026-10-30-185558.mp4', 1338, 240),
    ('october28_formal', '8792912 - 咩栗/blive_8792912_2026-10-28-182633.mp4', 9110.43, 53.69),
    ('october25_formal', '8792912 - 咩栗/blive_8792912_2026-10-25-185614.mp4', 83.56, 159.09),
    ('today_manual', '8792912 - 咩栗/blive_8792912_2026-10-30-185558.mp4', 1420.16, 53.33),
]
results = []
try:
    for name, rel, start, duration in cases:
        began = time.monotonic()
        source = root / 'Videos' / rel
        window = {'start_seconds': start, 'duration_seconds': duration}
        if name == 'today':
            task = json.loads(Path(str(source) + '.task.json').read_text(encoding='utf-8'))
            source = Path(next(c['path'] for c in task['candidates'] if c['start'] == start))
            window = {}
        result = pipeline._transcribe(source,
            hotwords=subtitle_hotwords_for_profile(profile_for_source(root / 'Videos', rel)), **window)
        assert not result.get('error'), result
        assert result['mode'] == 'sequential'
        lines = result['segments']
        results.append({'sample': name, 'source': rel, 'origin': start,
                        'elapsed_seconds': round(time.monotonic() - began, 2), **result})
        (out / 'fixed_adapter.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        if name == 'today':
            assert any('语序' in v['text'] and 109 < v['start'] < 115 for v in lines)
            assert any('不是那个意思' in v['text'] and 112 < v['start'] < 120 for v in lines)
        elif name == 'october28_formal':
            assert len(lines) >= 25
            assert any('价值观' in v['text'] for v in lines)
            assert not any('明镜与点点' in v['text'] for v in lines)
        elif name == 'october25_formal':
            assert len(lines) >= 30
            # A gap alone is not proof of lost dialogue: this sample includes
            # extended nonverbal sounds. Preserve its actual spoken sentences.
            assert any('上班是别的人格' in v['text'] for v in lines)
            assert not any('明镜与点点' in v['text'] for v in lines)
        else:
            assert any('不是那个意思' in v['text'] and 30 < v['start'] < 38 for v in lines)
            assert sum(1 for v in lines if 25 < v['start'] < 45) >= 3
        print(json.dumps({'sample': name, 'lines': len(lines), 'verified': True,
                          'elapsed_seconds': results[-1]['elapsed_seconds']}, ensure_ascii=False), flush=True)
finally:
    unload_asr_models()
