"""Persist local candidate context between overnight judging and local rendering."""
from __future__ import annotations

from functools import partial
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from bilive.analysis.batch_transport import build_batch_line, parse_batch_result, write_batch_parts
from bilive.domain.models import task_id_from_source_relpath
from bilive.media.finalize import PipelineError


def _checked_source(pipeline, rel, task_id):
    if task_id != task_id_from_source_relpath(rel):
        raise PipelineError('录播任务与源路径不匹配')
    source = pipeline.workbench.source(rel)
    stat = source.stat()
    return source, [stat.st_size, stat.st_mtime_ns]


def prepare(pipeline, rel, task_id, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    plan_path = folder / 'analysis-plan.json'
    source, signature = _checked_source(pipeline, rel, task_id)
    if plan_path.is_file():
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        if plan['source_relpath'] != rel or plan['source_signature'] != signature:
            raise PipelineError('源录播已变更，不能复用已有批量分析任务')
        return [folder / part['file'] for part in plan['parts']]

    if signature[0] < pipeline.settings.video.min_source_size_mb * 1048576:
        duration, profile, bindings = 0, {}, []
        history = {'source_relpath': rel, 'candidates': [], 'segments': [],
                   'skipped': True, 'skip_reason': 'below min_source_size_mb'}
    else:
        source, duration, danmaku, profile, history, bindings = pipeline._recall(rel, task_id)
    history.update(status='batch_waiting', batch_stage='preparing', batch_run=uuid4().hex)
    history_path = source.with_suffix('.mp4.task.json')
    save_progress = partial(pipeline._save_history, history_path, history)
    save_progress()
    plan = {'source_relpath': rel, 'source_signature': signature, 'duration': duration,
            'artist': profile.get('display_name') or Path(rel).parent.name,
            'model': pipeline.settings.mimo.model, 'batch_run': history['batch_run'], 'transcripts': {}}

    def request_lines():
        # Stream each encoded video into its file before encoding the next.
        for i, (event, candidate, entry) in enumerate(bindings):
            custom_id = f'candidate-{i + 1}'
            entry['custom_id'] = custom_id
            prepared = pipeline._prepare(source, rel, event, candidate, entry, danmaku, profile, save_progress,
                                         build_line=partial(build_batch_line, custom_id, pipeline.settings.mimo))
            if 'error' in prepared:
                entry.update(state='failed', prepare_error=prepared['error'])
                continue
            plan['transcripts'][custom_id] = prepared['transcript']
            entry['state'] = 'batch_waiting'
            yield custom_id, prepared['results']

    parts = write_batch_parts(folder, request_lines())
    plan['parts'] = [{'file': path.name, 'custom_ids': ids} for path, ids in parts]
    save_progress()
    pipeline._save_history(plan_path, plan)
    return [path for path, _ in parts]


def apply(pipeline, rel, task_id, folder, rows):
    source, signature = _checked_source(pipeline, rel, task_id)
    plan = json.loads((Path(folder) / 'analysis-plan.json').read_text(encoding='utf-8'))
    history = json.loads(source.with_suffix('.mp4.task.json').read_text(encoding='utf-8'))
    if (plan['source_relpath'] != rel or plan['source_signature'] != signature
            or history.get('batch_run') != plan['batch_run']):
        raise PipelineError('录播或分析版本已变更，不能写入旧批次结果')

    entries = history['candidates']
    if history['status'] == 'done' and history.get('batch_stage') == 'completed':
        return pipeline._analysis_summary(history)
    expected = {cid for part in plan['parts'] for cid in part['custom_ids']}
    if rows.keys() - expected:
        raise PipelineError('返回了本场批次之外的候选编号')

    bindings, prepared = [], []
    for entry in entries:
        event = SimpleNamespace(**entry)
        candidate = pipeline.workbench.artifacts / 'candidates' / task_id / (
            f'candidate-{round(event.start * 1000)}-{round(event.end * 1000)}.mp4')
        if Path(entry['path']).resolve() != candidate.resolve():
            raise PipelineError('候选文件与本次批量录播的时间范围不匹配')
        bindings.append((event, candidate, entry))
        cid = entry['custom_id']
        if entry.get('prepare_error') or cid not in rows:
            prepared.append({'error': entry.get('prepare_error') or '批量任务未返回此候选的结果'})
            continue
        row = rows[cid]
        try:
            if row.get('custom_id') != cid:
                raise ValueError('批量候选编号不匹配')
            judgments = parse_batch_result(row, artist=plan['artist'], model=plan['model'],
                                           duration=event.end - event.start)
            prepared.append({'results': judgments, 'transcript': plan['transcripts'].get(cid),
                             'usage': row['response']['body'].get('usage', {})})
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
            prepared.append({'error': str(exc)})
    return pipeline._apply_results(rel, source, plan['duration'], history, bindings, prepared)
