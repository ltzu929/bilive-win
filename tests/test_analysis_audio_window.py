from pathlib import Path
from types import SimpleNamespace

import pytest

from bilive.analysis.pipeline import AnalysisPipeline
from bilive.config.settings import Settings


@pytest.mark.parametrize('start,end,expected', [
    (20.25, 30.75, (14.25, 22.5)),
    (1.25, 3.75, (0, 9.75)),
    (95.25, 99.75, (89.25, 10.75)),
])
def test_formal_recognition_extracts_only_the_padded_source_window(monkeypatch, start, end, expected):
    settings = Settings()
    settings.subtitle.snap_enabled = False
    calls = []
    source = Path('source.mp4')

    def transcribe(video, **window):
        calls.append((video, window))
        origin, duration = expected
        return {'segments': [{'start': start-origin, 'end': end-origin, 'text': '完整互动'}]}

    monkeypatch.setattr('bilive.analysis.pipeline.ensure_raw_cut',
                        lambda *args: pytest.fail('ASR must not transcode a video window'))
    pipeline = AnalysisPipeline(settings, None, None, transcribe=transcribe)
    actual_start, actual_end, lines, alignment = pipeline._formal_subtitles(
        source, 'segment', start, end, 100, SimpleNamespace(start=0, end=10), None)
    assert calls == [(source, {'start_seconds': expected[0], 'duration_seconds': expected[1]})]
    assert (actual_start, actual_end) == (start, end)
    assert [(line.start, line.end, line.text) for line in lines] == [(0, end-start, '完整互动')]
    assert alignment['asr_origin'] == expected[0]
    assert alignment['asr_duration'] == expected[1]


def test_reusable_auxiliary_transcript_skips_formal_recognition():
    settings = Settings()
    settings.subtitle.snap_enabled = False
    pipeline = AnalysisPipeline(settings, None, None,
                                transcribe=lambda *a, **kw: pytest.fail('valid auxiliary ASR must be reused'))
    transcript = {'segments': [{'start': 10, 'end': 20, 'text': '原识别字幕'}]}
    _, _, lines, alignment = pipeline._formal_subtitles(
        Path('source.mp4'), 'segment', 10, 20, 100, SimpleNamespace(start=0, end=40), transcript)
    assert lines[0].text == '原识别字幕'
    assert alignment['asr_origin'] == 0
