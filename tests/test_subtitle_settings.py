import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bilive.config.settings import load_settings, save_subtitle_style
from bilive.db.conn import init_upload_db
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench
from bilive.upload.queue import UploadQueue

STYLE = {'font_name': 'Microsoft YaHei', 'font_size': 48, 'margin_v': 110, 'outline': 2,
         'alignment': 2, 'primary_colour': '&H00FFFFFF', 'outline_colour': '&H00000000'}


def test_style_persistence_preserves_other_config_and_legacy_values(tmp_path):
    config = tmp_path / 'settings.toml'
    prior = '# keep this comment\n[worker]\nidle_timeout=42\n[slice.subtitle]\nfont_name="Old Font"\nfont_size=20\nmargin_v=60\n[slice.edit]\nfont_size=24\n[paths]\ncustom="unchanged"\n'
    config.write_text(prior, encoding='utf-8')
    assert save_subtitle_style(config, STYLE) == STYLE
    assert config.read_text(encoding='utf-8').startswith(prior)
    assert load_settings(config).subtitle_style() == STYLE
    again = dict(STYLE, font_size=56, outline=3)
    assert save_subtitle_style(config, again) == again
    assert config.read_text(encoding='utf-8').count('[edit]') == 1
    assert load_settings(config).worker.idle_timeout == 42


def test_top_level_edit_takes_precedence_over_legacy_subtitle(tmp_path):
    config = tmp_path / 'settings.toml'
    config.write_text('[slice.subtitle]\nfont_name="Legacy"\nfont_size=20\n[edit]\nfont_family="New"\nfont_size=48\n', encoding='utf-8')
    s = load_settings(config)
    assert s.edit.font_family == 'New' and s.edit.font_size == 48


@pytest.mark.parametrize('style', [dict(STYLE, font_size=0), dict(STYLE, margin_v=1081),
                                    dict(STYLE, alignment=10), dict(STYLE, font_name='bad\nfont'),
                                    dict(STYLE, primary_colour='#ffffff'), dict(STYLE, unknown=True)])
def test_invalid_style_does_not_change_config(tmp_path, style):
    config = tmp_path / 'settings.toml'
    config.write_text('[worker]\nidle_timeout=42\n', encoding='utf-8')
    prior = config.read_bytes()
    with pytest.raises(ValueError):
        save_subtitle_style(config, style)
    assert config.read_bytes() == prior


def test_style_api_persists_and_new_manual_segments_refresh_defaults(tmp_path, monkeypatch):
    videos = tmp_path / 'Videos'
    videos.mkdir()
    (videos / 'session.mp4').write_bytes(b'isolated source; no media operation')
    config = tmp_path / 'settings.toml'
    config.write_text('[slice.edit]\nfont_size=20\nmargin_v=60\n', encoding='utf-8')
    settings = load_settings(config)
    db = tmp_path / 'queue.db'
    init_upload_db(db)
    wb = Workbench(videos, tmp_path / 'artifacts', ActionJobStore(tmp_path / 'jobs'),
                   SubtitleStore(tmp_path / 'subtitles'), UploadQueue(db), settings.subtitle_style())
    wb.default_style_provider = lambda: load_settings(config).subtitle_style()
    old = wb.points('session.mp4', 0, 1, 'old', 'test')
    api = importlib.import_module('bilive.api.app')
    monkeypatch.setattr(api, 'settings', settings)
    monkeypatch.setattr(api, 'workbench', wb)
    c = TestClient(api.app)
    response = c.post('/api/config/subtitle-style', json={'style': STYLE})
    assert response.status_code == 200, response.text
    assert response.json()['style'] == STYLE
    assert settings.subtitle_style() == STYLE and load_settings(config).subtitle_style() == STYLE
    new = wb.points('session.mp4', 1, 2, 'new', 'test')
    assert new['subtitle_style'] == STYLE
    assert wb.load(old['segment_id']) == old
    # A worker already alive refreshes the persisted defaults when it creates a segment.
    save_subtitle_style(config, dict(STYLE, font_size=60))
    assert wb.points('session.mp4', 2, 3, 'later', 'test')['subtitle_style']['font_size'] == 60
