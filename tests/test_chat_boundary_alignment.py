"""Protect selected chat context when nearby cue boundaries compete."""
from types import SimpleNamespace

import pytest

from bilive.analysis.pipeline import AnalysisPipeline
from bilive.config.settings import Settings


@pytest.mark.parametrize('enabled', [True, False])
def test_formal_chat_alignment_never_snaps_inward_to_closer_cues(enabled):
    settings = Settings()
    settings.subtitle.padding = 0
    settings.subtitle.snap_tolerance = 1
    settings.subtitle.snap_enabled = enabled
    pipeline = AnalysisPipeline(settings, None, None)
    transcript = {'segments': [
        {'start': 2.0, 'end': 2.45, 'text': '事情的起因'},
        {'start': 2.5, 'end': 6.5, 'text': '中间的互动'},
        {'start': 6.55, 'end': 7.0, 'text': '最后的回应'},
    ]}
    start, end, lines, alignment = pipeline._formal_subtitles(
        None, 'isolated', 2.4, 6.6, 10, SimpleNamespace(start=0, end=10), transcript)
    assert (start, end) == ((2.0, 7.0) if enabled else (2.4, 6.6))
    assert [line.text for line in lines] == ['事情的起因', '中间的互动', '最后的回应']
    assert lines[0].start == pytest.approx(0)
    assert lines[-1].end == pytest.approx(end-start)
    assert alignment['original_start'] == 2.4 and alignment['original_end'] == 6.6


def test_chat_boundary_outside_tolerance_does_not_expand_range():
    settings = Settings()
    settings.subtitle.padding = 0
    settings.subtitle.snap_tolerance = .1
    pipeline = AnalysisPipeline(settings, None, None)
    start, end, _, _ = pipeline._formal_subtitles(None, 'isolated', 2, 4, 10,
        SimpleNamespace(start=0, end=10),
        {'segments': [{'start': 1, 'end': 5, 'text': '超出容差的整句'}]})
    assert (start, end) == (2, 4)


@pytest.mark.parametrize('tolerance,expected', [
    (0, (2.4, 6.6)),
    (.1, (2.4, 6.6)),
    (.5, (2.0, 7.0)),
    (3, (2.0, 7.0)),
])
def test_chat_alignment_uses_nearest_outward_boundary(tolerance, expected):
    settings = Settings()
    settings.subtitle.padding = 0
    settings.subtitle.snap_tolerance = tolerance
    pipeline = AnalysisPipeline(settings, None, None)
    start, end, lines, _ = pipeline._formal_subtitles(None, 'isolated', 2.4, 6.6, 10,
        SimpleNamespace(start=0, end=10), {'segments': [
            {'start': 0, 'end': 1.8, 'text': '此前无关话题'},
            {'start': 2.0, 'end': 2.45, 'text': '事情的起因'},
            {'start': 2.5, 'end': 6.5, 'text': '中间的互动'},
            {'start': 6.55, 'end': 7.0, 'text': '最后的回应'},
            {'start': 7.2, 'end': 9.0, 'text': '之后无关话题'},
        ]})
    assert (start, end) == expected
    assert [line.text for line in lines] == ['事情的起因', '中间的互动', '最后的回应']
    assert all(0 <= line.start < line.end <= end-start for line in lines)


def test_chat_alignment_at_source_edges_stays_inside_recording():
    settings = Settings()
    settings.subtitle.snap_tolerance = 3
    pipeline = AnalysisPipeline(settings, None, None)
    start, end, lines, _ = pipeline._formal_subtitles(None, 'isolated', .2, 9.8, 10,
        SimpleNamespace(start=0, end=10), {'segments': [
            {'start': 0, 'end': 4, 'text': '开场铺垫'},
            {'start': 4, 'end': 10, 'text': '最后回应'},
        ]})
    assert (start, end) == (0, 10)
    assert lines[0].start == 0 and lines[-1].end == 10
