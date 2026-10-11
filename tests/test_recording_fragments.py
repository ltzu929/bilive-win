import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bilive.recorder import fragments
from bilive.recorder.events import queue_completed_recording
from bilive.jobs.action_jobs import ActionJobStore


@pytest.mark.parametrize('duration,expected', [(4.282, True), (10.687, True),
                                              (29.999, True), (30, False),
                                              (300, False), (0, False), ('nan', False)])
def test_duration_boundary(tmp_path, monkeypatch, duration, expected):
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'media')
    monkeypatch.setattr(fragments, 'run_media_command', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=json.dumps({'format': {'duration': duration}})))
    fragments._short_duration.cache_clear()
    assert fragments.is_recording_fragment(source) is expected


def test_completed_fragment_is_preserved_without_remux_job(tmp_path, monkeypatch):
    videos = tmp_path / 'Videos'
    source = videos / '123 - 主播' / 'session.flv'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'fragment')
    source.with_suffix('.xml').write_text('<i/>')
    monkeypatch.setattr(fragments, 'is_recording_fragment', lambda p: True)
    jobs = ActionJobStore(tmp_path / 'jobs')
    assert queue_completed_recording(videos, jobs, str(source)) is None
    assert not jobs.busy() and not source.exists()
    archived = tmp_path / 'artifacts/recording-fragments/123 - 主播/session.flv'
    assert archived.read_bytes() == b'fragment'
    assert archived.with_suffix('.xml').read_text() == '<i/>'


def test_probe_failure_preserves_source(tmp_path, monkeypatch):
    source = tmp_path / 'broken.mp4'
    source.write_bytes(b'broken')
    monkeypatch.setattr(fragments, 'run_media_command', lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout=''))
    fragments._short_duration.cache_clear()
    assert not fragments.is_recording_fragment(source)
    assert source.exists()


def test_large_recording_does_not_need_probe(tmp_path, monkeypatch):
    source = tmp_path / 'video.mp4'
    with source.open('wb') as stream:
        stream.truncate(fragments.MAX_FRAGMENT_BYTES)
    monkeypatch.setattr(fragments, 'run_media_command', lambda *a, **kw: pytest.fail('unexpected probe'))
    assert not fragments.is_recording_fragment(source)
