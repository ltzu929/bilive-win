import json

import pytest

from bilive.analysis.pipeline import AnalysisPipeline
from bilive.domain.models import task_id_from_source_relpath
from bilive.media.finalize import PipelineError
from bilive.vendor.autoslice.burst_detector import BurstEvent
from test_analysis_pipeline import setup


TASK = task_id_from_source_relpath('session.mp4')


def response(cid, clips):
    return {'custom_id': cid, 'response': {'status_code': 200, 'body': {
        'model': 'mimo-v2.6-flash', 'usage': {'prompt_tokens': 42},
        'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'clips': clips})}}]}}}


def clip():
    return {'decision': 'keep', 'reason': '完整', 'title': '一段回应', 'description': '回应观众',
            'tags': [], 'trim_start': 0.2, 'trim_end': 1.8,
            'quality_score': .95, 'completeness_score': .95, 'confidence': .95}


@pytest.fixture
def batch(setup, tmp_path, monkeypatch):
    settings, wb, fin, _ = setup
    settings.video.min_source_size_mb = 0
    settings.analysis.pre_judge_asr = False
    settings.subtitle.padding = 0
    calls = []

    def encode(cid, mimo, **kwargs):
        calls.append(cid)
        return json.dumps({'custom_id': cid}).encode() + b'\n'

    def transcribe(path, **window):
        return {'segments': [{'start': 0, 'end': window['duration_seconds'], 'text': '台词'}]}

    monkeypatch.setattr('bilive.analysis.batch_pipeline.build_batch_line', encode)
    pipeline = AnalysisPipeline(settings, wb, fin, judge=lambda **kw: pytest.fail('unexpected realtime call'),
                                transcribe=transcribe)
    return pipeline, tmp_path / 'batch', calls


def test_prepare_apply_and_reopen_do_not_repeat_work(batch, monkeypatch):
    pipeline, folder, calls = batch
    source = 'session.mp4'
    parts = pipeline.prepare_batch(source, TASK, folder)
    assert pipeline.prepare_batch(source, TASK, folder) == parts
    assert calls == ['candidate-1']
    assert json.loads(parts[0].read_bytes())['custom_id'] == calls[0]
    history_path = pipeline.workbench.source(source).with_suffix('.mp4.task.json')
    assert json.loads(history_path.read_text(encoding='utf-8'))['status'] == 'batch_waiting'
    rows = {calls[0]: response(calls[0], [clip()])}

    # Simulate power loss after saving subtitles, before creating the final. On
    # restart the local importer must resume, without another ASR or AI call.
    original_finalize = pipeline.finalizer.finalize

    def power_loss(*args, **kwargs):
        raise SystemExit

    monkeypatch.setattr(pipeline.finalizer, 'finalize', power_loss)
    with pytest.raises(SystemExit):
        pipeline.apply_batch(source, TASK, folder, rows)
    monkeypatch.setattr(pipeline.finalizer, 'finalize', original_finalize)
    monkeypatch.setattr(pipeline, 'transcribe', lambda *a, **kw: pytest.fail('duplicate transcription'))
    result = pipeline.apply_batch(source, TASK, folder, rows)
    sid, = result['segments']
    segment = pipeline.workbench.detail(sid)
    assert segment['final_valid']
    assert pipeline.workbench.uploads.get_by_path(segment['final_path'])['stage'] == 'staged'
    history = json.loads(history_path.read_text(encoding='utf-8'))
    assert history['candidates'][0]['token_usage'] == {'prompt_tokens': 42}
    assert history['candidates'][0]['segments'] == [sid]
    assert history['batch_stage'] == 'completed'
    parts[0].unlink()
    assert pipeline.apply_batch(source, TASK, folder, rows) == result


def test_preserves_previous_manual_segment_and_source_version(batch):
    pipeline, folder, calls = batch
    source = 'session.mp4'
    pipeline.prepare_batch(source, TASK, folder)
    path = pipeline.workbench.source(source).with_suffix('.mp4.task.json')
    history = json.loads(path.read_text(encoding='utf-8'))
    start = history['candidates'][0]['start']
    manual = pipeline.workbench.points(source, start + .2, start + 1.8, '人工保留的标题', '人工选择')
    rows = {calls[0]: response(calls[0], [clip()])}
    with pytest.raises(PipelineError, match='之外'):
        pipeline.apply_batch(source, TASK, folder, {**rows, 'foreign': {}})
    result = pipeline.apply_batch(source, TASK, folder, rows)
    assert result['requires_source_review']
    assert pipeline.workbench.load(manual['segment_id']) == manual
    history['batch_run'] = 'different'
    pipeline._save_history(path, history)
    with pytest.raises(PipelineError, match='变更'):
        pipeline.apply_batch(source, TASK, folder, rows)


def test_missing_failed_and_empty_are_individual_outcomes(batch, monkeypatch):
    pipeline, folder, _ = batch
    monkeypatch.setattr('bilive.analysis.pipeline.detect_bursts', lambda *a, **kw: [
        BurstEvent(i, i, i + 1.5, 1.5, 10, 1, 10) for i in (0, 2, 4)])
    pipeline.prepare_batch('session.mp4', TASK, folder)
    rows = {'candidate-3': response('candidate-3', []),
            'candidate-2': {'custom_id': 'candidate-2', 'error': {'code': 'inference_failed'}}}
    result = pipeline.apply_batch('session.mp4', TASK, folder, rows)
    history = json.loads(pipeline.workbench.source('session.mp4').with_suffix('.mp4.task.json').read_text('utf-8'))
    assert result['requires_source_review'] and result['segments'] == [] and result['failed_candidates'] == 2
    assert [c['state'] for c in history['candidates']] == ['failed', 'failed', 'empty']
    assert history['candidates'][2]['token_usage']['prompt_tokens'] == 42
    assert all(c['batch_applied'] for c in history['candidates'])


def test_small_recording_skips_before_encoding(batch):
    pipeline, folder, calls = batch
    pipeline.settings.video.min_source_size_mb = 100
    assert pipeline.prepare_batch('session.mp4', TASK, folder) == []
    assert pipeline.apply_batch('session.mp4', TASK, folder, {})['candidates'] == 0
    assert not calls
