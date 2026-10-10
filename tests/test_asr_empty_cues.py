from contextlib import nullcontext
from types import SimpleNamespace
from pathlib import Path

import pytest

from bilive.analysis.pipeline import AnalysisPipeline
from bilive.config.settings import Settings
from bilive.media.finalize import PipelineError


def pipeline(monkeypatch, transcript):
    segment = {'source_relpath': 'sample.mp4', 'source_start': 10, 'source_end': 14}
    wb = SimpleNamespace(videos=Path('Videos'), lock=nullcontext, load=lambda sid: segment,
        check_revision=lambda s, revision: None, check_source_idle=lambda rel: None,
        source=lambda rel: rel)
    monkeypatch.setattr('bilive.analysis.pipeline._ffprobe_duration', lambda *args: 20)
    settings = Settings()
    settings.subtitle.padding = 0
    settings.subtitle.snap_enabled = False
    settings.subtitle.light_correct = False
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


@pytest.mark.parametrize('formal', [False, True], ids=['manual', 'formal'])
@pytest.mark.parametrize('start,end', [
    (float('nan'), 1), (0, float('inf')), (-0.1, 1), (2, 1), (0, 5),
], ids=['nonfinite-start', 'nonfinite-end', 'negative', 'reversed', 'out-of-range'])
def test_invalid_blank_cues_still_fail_before_being_skipped(monkeypatch, formal, start, end):
    transcript = {'segments': [
        {'start': 0, 'end': 1, 'text': '有效字幕'},
        {'start': start, 'end': end, 'text': '   '},
    ]}
    p = pipeline(monkeypatch, transcript)
    with pytest.raises((ValueError, PipelineError)):
        if formal:
            p._formal_subtitles('sample.mp4', 'sample', 0, 4, 4,
                SimpleNamespace(start=0, end=4), transcript)
        else:
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


def test_manual_subtitle_correction_changes_only_draft_text(monkeypatch):
    from bilive.vendor.autoslice.analysis_result import TranscriptSegment
    p = pipeline(monkeypatch, {'segments': [{'start': 0.2, 'end': 1.4, 'text': '谢谢上剑'}]})
    p.settings.subtitle.light_correct = True
    p.correct = lambda lines, **kw: [TranscriptSegment(v.start, v.end, '谢谢上舰') for v in lines]
    result = p.transcribe_segment(SimpleNamespace(target='sample', payload={'revision': 1}))
    assert result['lines'] == [{'index': 1, 'start': 0.2, 'end': 1.4, 'text': '谢谢上舰'}]
    assert result['correction'] == {'status': 'corrected', 'changed_lines': 1}


@pytest.mark.parametrize('bad_output', ['timestamp', 'empty', 'count', 'failure'])
def test_manual_correction_failure_preserves_original_draft(monkeypatch, bad_output):
    p = pipeline(monkeypatch, {'segments': [{'start': 0.2, 'end': 1.4, 'text': '原始字幕'}]})
    p.settings.subtitle.light_correct = True
    def correct(lines, **kwargs):
        if bad_output == 'failure':
            raise RuntimeError('isolated unavailable correction service')
        if bad_output == 'timestamp':
            lines[0].start = 99
        elif bad_output == 'empty':
            lines[0].text = ''
        else:
            return []
        return lines
    p.correct = correct
    result = p.transcribe_segment(SimpleNamespace(target='sample', payload={'revision': 1}))
    assert result['lines'] == [{'index': 1, 'start': 0.2, 'end': 1.4, 'text': '原始字幕'}]
    assert result['correction'] == {'status': 'failed_original_retained', 'changed_lines': 0}


def test_manual_recognition_and_correction_share_saved_streamer_hotwords(monkeypatch, tmp_path):
    from bilive.vendor.streamer_profiles import patch_streamer_profile
    transcript = {'segments': [{'start': 0.2, 'end': 1.4, 'text': '白露'}]}
    p = pipeline(monkeypatch, transcript)
    p.workbench.videos = tmp_path
    p.workbench.load('sample')['source_relpath'] = '123 - 主播甲/session.mp4'
    p.settings.subtitle.light_correct = True
    patch_streamer_profile(tmp_path, '123', {'display_name': '主播甲', 'aliases': ['甲甲'],
        'subtitle_hotwords': ['白露', '匹诺康尼']})
    calls = []
    p.transcribe = lambda *args, **kw: calls.append(('asr', kw)) or transcript
    p.correct = lambda lines, **kw: calls.append(('correction', kw)) or lines
    result = p.transcribe_segment(SimpleNamespace(target='sample', payload={'revision': 1}))
    assert result['lines'] == [{'index': 1, 'start': 0.2, 'end': 1.4, 'text': '白露'}]
    assert [kw['hotwords'] for _, kw in calls] == [['主播甲', '甲甲', '白露', '匹诺康尼']] * 2
    assert calls[0][1]['start_seconds'] == 10 and calls[0][1]['duration_seconds'] == 4
    patch_streamer_profile(tmp_path, '123', {'subtitle_hotwords': ['新角色']})
    calls.clear()
    p.transcribe_segment(SimpleNamespace(target='sample', payload={'revision': 1}))
    assert [kw['hotwords'] for _, kw in calls] == [['主播甲', '甲甲', '新角色']] * 2
