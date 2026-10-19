import importlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bilive.config.settings import load_settings, save_subtitle_style
from bilive.domain.models import SubtitleLine
from bilive.media.subtitle_store import SubtitleStore
from bilive.vendor.subtitle_style import SubtitleStyle, validate_style


STYLE = dict(font_name='Douyin Sans', font_size=48, margin_v=110, alignment=2,
             primary_colour='&H005959D9', outline_colour='&H00FFFFFF', outline=2,
             bold=True, spacing=5.76, shadow=3, shadow_outline=10, shadow_colour='&H00ADADEE')


def test_pr_style_survives_both_settings_apis_and_optional_fields_clear(tmp_path, monkeypatch):
    config = tmp_path / 'settings.toml'
    config.write_text('[worker]\nidle_timeout=42\n', encoding='utf-8')
    assert save_subtitle_style(config, STYLE) == STYLE
    api = importlib.import_module('bilive.api.app')
    monkeypatch.setattr(api, 'settings', load_settings(config))
    monkeypatch.setattr(api.workbench, 'default_style', dict(api.workbench.default_style))
    client = TestClient(api.app)
    response = client.post('/api/config/settings', json={'changes': {}, 'style': STYLE})
    assert response.status_code == 200, response.text
    assert response.json()['style'] == STYLE
    assert load_settings(config).subtitle_style() == STYLE
    response = client.post('/api/config/settings', json={'changes': {}, 'style': {
        'font_name': 'Arial', 'font_size': 48, 'margin_v': 110}})
    assert response.status_code == 200, response.text
    assert 'shadow_outline' not in load_settings(config).subtitle_style()
    assert load_settings(config).worker.idle_timeout == 42


@pytest.mark.parametrize('extra', [dict(bold=1), dict(spacing=float('nan')),
                                    dict(shadow=-1), dict(shadow_outline=31),
                                    dict(shadow_colour='bad')])
def test_invalid_pr_effects_rejected(extra):
    with pytest.raises(ValueError):
        validate_style({**STYLE, **extra})


def test_pr_styles_burn_actual_colours_and_expanded_shadow(tmp_path):
    store = SubtitleStore(tmp_path / 'subs')
    store.save('clip', 1, [SubtitleLine(1, 0, 1, '字幕样式测试')], 1)
    images = []
    for name, colour, shadow in [('red', '&H005959D9', '&H00ADADEE'),
                                 ('blue', '&H00D99859', '&H00EECBAD')]:
        style = {**STYLE, 'primary_colour': colour, 'shadow_colour': shadow}
        validate_style(style)
        assert SubtitleStyle.from_mapping(style).to_mapping() == style
        ass = store.write_ass('clip', tmp_path / f'{name}.ass', style)
        text = ass.read_text(encoding='utf-8')
        assert ',48,' in text and ',100,100,5.76,' in text
        assert 'Style: Shadow' in text and ',10.0,0,2,' in text
        assert 'Dialogue: 1,' in text
        arg = str(ass).replace('\\', '/').replace(':', '\\:')
        result = subprocess.run(['ffmpeg', '-v', 'info', '-f', 'lavfi', '-i',
                                 'color=c=black:s=1920x1080:r=1:d=1', '-vf',
                                 f"subtitles=filename='{arg}'", '-frames:v', '1',
                                 '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
                                capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        pixels = result.stdout
        assert len(pixels) == 1920 * 1080 * 3
        rgb = tuple(int(colour[i:i + 2], 16) for i in (8, 6, 4))
        shade = tuple(int(shadow[i:i + 2], 16) for i in (8, 6, 4))
        # Actual fill and enlarged shadow both survive libass rendering.
        for target in (rgb, shade):
            assert sum(all(abs(pixels[i + c] - target[c]) < 14 for c in range(3))
                       for i in range(0, len(pixels), 3)) > 30
        images.append(pixels)
    assert images[0] != images[1]
