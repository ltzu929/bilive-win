from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from bilive.analysis.pipeline import AnalysisPipeline
from bilive.config.settings import Settings
from bilive.media.finalize import PipelineError


def pipeline(monkeypatch, transcript):
    segment = {'source_relpath': 'sample.mp4', 'source_start': 10, 'source_end': 14}
    wb = SimpleNamespace(lock=nullcontext, load=lambda sid: segment,
        check_revision=lambda s, revision: None, check_source_idle=lambda rel: None,
        source=lambda rel: rel)
    monkeypatch.setattr('bilive.analysis.pipeline._ffprobe_duration', lambda *args: 20)
    settings = Settings()
    settings.subtitle.padding = 0
    settings.subtitle.snap_enabled = False
    return AnalysisPipeline(settings, wb, None, transcribe=lambda *a, **kw: transcript)


def cues():
    return [{'start': 0, 'end': 1, 'text': '第一句'},
            {'start': 1, 'end': 2, 'text': '   '},
            {'start': 2, 'end': 3, 'text': ''},
            {'start': 3, 'end': 4, 'text': '第二句'}]


def test_manual_asr_ignores_blank_cues_without_shifting_valid_times(monkeypatch):
    p = pipeline(monkeypatch, {'segments': cues()})
    result = p.transcribe_segment(SimpleNamespace(target='sample', payload={'revision': 1}))
    assert result['lines'] == [
        {'index': 1, 'start': 0, 'end': 1, 'text': '第一句'},
        {'index': 2, 'start': 3, 'end': 4, 'text': '第二句'},
    ]


@pytest.mark.parametrize('values', [[], [{'start': 0, 'end': 1, 'text': '   '}]])
def test_all_empty_asr_reports_no_valid_subtitles(monkeypatch, values):
    p = pipeline(monkeypatch, {'segments': values})
    with pytest.raises(PipelineError, match='没有返回有效字幕'):
        p.transcribe_segment(SimpleNamespace(target='sample', payload={'revision': 1}))


def test_formal_asr_ignores_blank_cues_and_keeps_source_alignment(monkeypatch):
    p = pipeline(monkeypatch, {})
    start, end, lines, _ = p._formal_subtitles('sample.mp4', 'sample', 0, 4, 4,
        SimpleNamespace(start=0, end=4), {'segments': cues()})
    assert (start, end) == (0, 4)
    assert [(l.index, l.start, l.end, l.text) for l in lines] == [
        (1, 0, 1, '第一句'), (2, 3, 4, '第二句')]


def test_formal_asr_keeps_specific_adapter_error(monkeypatch):
    p = pipeline(monkeypatch, {})
    with pytest.raises(PipelineError, match='decoder compatibility error'):
        p._formal_subtitles('sample.mp4', 'sample', 0, 4, 4,
            SimpleNamespace(start=0, end=4), {'segments': [], 'error': 'decoder compatibility error'})


def test_nonempty_invalid_cue_still_fails(monkeypatch):
    p = pipeline(monkeypatch, {'segments': [{'start': 0, 'end': 20, 'text': '错误范围'}]})
    with pytest.raises(ValueError):
        p.transcribe_segment(SimpleNamespace(target='sample', payload={'revision': 1}))


def test_auxiliary_asr_with_blank_cues_remains_reusable(monkeypatch, tmp_path):
    transcript = {'segments': cues(), 'transcript': '第一句第二句'}
    p = pipeline(monkeypatch, transcript)
    p.settings.analysis.pre_judge_asr = True
    p.judge = lambda **kwargs: []
    monkeypatch.setattr('bilive.analysis.pipeline.ensure_raw_cut', lambda *args: None)
    entry = {}
    prepared = p._prepare('sample.mp4', 'sample.mp4',
        SimpleNamespace(start=0, end=4, burst_start=0, burst_end=4), tmp_path / 'candidate.mp4', entry, [], {})
    assert prepared['transcript'] == transcript
    assert not entry.get('pre_asr_failed')
