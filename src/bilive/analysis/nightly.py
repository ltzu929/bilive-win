"""Durable handoff between verified recordings, the nightly cutoff and MiMo."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import time
from pathlib import Path

from bilive.analysis.tasks import RecordingTasks
from bilive.config.secrets import redact
from bilive.domain.models import source_relpath_from_task_id, task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionConflict
from bilive.lifecycle.profiles import profile_for_source
from bilive.worker.locks import LockUnavailable

BEIJING = timezone(timedelta(hours=8))
POLL_SECONDS = 20 * 60
logger = logging.getLogger(__name__)


def _cutoff(now):
    return datetime.fromtimestamp(now, BEIJING).replace(hour=23, minute=30, second=0, microsecond=0)


def _due_today(state, now):
    cutoff = _cutoff(now).timestamp()
    return state['registered'] <= cutoff <= now


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def _pending(wb, task):
    return wb.artifacts / 'nightly-batches' / (hashlib.sha256(task.encode()).hexdigest() + '.json')


def _folder(wb, task):
    return wb.artifacts / 'candidates' / task / 'nightly-batch'


def check_batch_idle(wb, task):
    if _pending(wb, task).is_file():
        raise ActionConflict('本场正在等待夜间批量分析或回填')


def _display(wb, state, stage, message, status='batch_waiting'):
    source = wb.source(state['source_relpath'])
    history = RecordingTasks(wb).history_for_write(source)
    history.update(source_relpath=state['source_relpath'], status=status,
                   batch_stage=stage, batch_message=message)
    history.setdefault('candidates', [])
    history.setdefault('segments', [])
    if status == 'batch_failed':
        history['requires_source_review'] = True
    _write(source.with_suffix('.mp4.task.json'), history)


def register_remux(wb, rel, *, now=None):
    """Called only after the recorder's FLV has finished and the MP4 is verified."""
    now = time.time() if now is None else now
    task = task_id_from_source_relpath(rel)
    path = _pending(wb, task)
    with wb.lock():
        if path.exists() or (_folder(wb, task) / 'receipt.json').is_file():
            return True
        profile = profile_for_source(wb.videos, rel)
        since = profile.get('nightly_batch_enabled_at')
        if not profile.get('nightly_batch_enabled') or not since or now < since:
            return False
        source = wb.source(rel)
        # Recovered remux jobs can refer to MP4s made before the opt-in.
        if source.stat().st_mtime < since:
            return False
        if (source.with_suffix('.mp4.task.json').exists() or wb.jobs.find_active('start_analysis', task)
                or wb.list(rel)):
            return True
        state = dict(task=task, source_relpath=rel, registered=now, enabled_at=since, phase='queued')
        _write(path, state)
        _display(wb, state, 'queued', '录播已就绪，正在等待准备夜间批量输入')
    return True


def _enabled(wb, state):
    profile = profile_for_source(wb.videos, state['source_relpath'])
    return (profile.get('nightly_batch_enabled') is True
            and profile.get('nightly_batch_enabled_at') == state['enabled_at'])


def schedule_tick(wb, control, now=None):
    """Only enqueue short-lived Worker work; cloud waits do not keep it running."""
    now = time.time() if now is None else now
    wake = False
    for path in (wb.artifacts / 'nightly-batches').glob('*.json'):
        try:
            with wb.lock():
                if not path.exists():
                    continue
                state = _read(path)
                task = state['task']
                if path != _pending(wb, task) or source_relpath_from_task_id(task) != state['source_relpath']:
                    raise ValueError('invalid batch recording identity')
                due = state.get('next_check', 0) <= now
                if state['phase'] == 'ready' and not _due_today(state, now) and _enabled(wb, state):
                    due = False
                if due:
                    wb.jobs.submit('nightly_batch', task)
                    wake = True
        except (FileNotFoundError, ActionConflict, LockUnavailable):
            continue
        except Exception:
            logger.exception('Cannot dispatch nightly recording %s', path.name)
    if wake:
        control.wake()


def _finish(wb, path, state, message=None):
    """Keep the cloud IDs with the source package. Release the hold last."""
    task = state['task']
    folder = _folder(wb, task)
    with wb.lock():
        try:
            source = wb.source(state['source_relpath'])
        except FileNotFoundError:
            source = None
        if source is not None:
            if message:
                _display(wb, state, 'failed', message, 'batch_failed')
            RecordingTasks(wb).marker(source, 'failed' if message else 'done')
        folder.mkdir(parents=True, exist_ok=True)
        state.update(phase='failed' if message else 'done', finished=time.time(), error=message)
        _write(folder / 'receipt.json', state)
        _release_inputs(folder, path)


def _release_inputs(folder, pending):
    for part in folder.glob('batch-input-*.jsonl'):
        part.unlink(missing_ok=True)
    pending.unlink(missing_ok=True)


def _permanent(exc):
    code = getattr(exc, 'status_code', 0) or 0
    return isinstance(exc, ValueError) or (400 <= code < 500 and code not in (408, 409, 425, 429))


def _remote_part(client, path, state, part, folder, can_create, now):
    """Check one independent file; persist the intent before a billable creation."""
    if part.get('done'):
        return
    output = folder / part['output']
    if output.is_file():
        part['done'] = True
        return
    if not part.get('batch_id'):
        if part.get('intent'):
            part['batch_id'] = client.find(part['file_id'])
            if not part['batch_id']:
                if now - part['intent'] > 26 * 3600:
                    raise ValueError('未能核实上次创建的云端批次。已停止自动提交，以免重复计费')
                return
        elif not can_create():
            return
        else:
            if not part.get('file_id'):
                part['file_id'] = client.upload(folder / part['file'])
                _write(path, state)
            if not can_create():
                return
            part['intent'] = now
            _write(path, state)
            try:
                part['batch_id'] = client.create(part['file_id'], 'bilive-' + path.stem[:20] + '-' + part['output'])
            except Exception as exc:
                if getattr(exc, 'status_code', None) == 429:
                    part.pop('intent')
                    _write(path, state)
                raise
        _write(path, state)
    status = client.status(part['batch_id'])
    phase = str(status['status']).lower()
    part['status'] = phase
    if phase not in {'completed', 'failed', 'expired', 'cancelled', 'canceled'}:
        return
    try:
        rows = client.results(status, part['custom_ids'])
    except Exception as exc:
        if getattr(exc, 'status_code', None) == 404 and now - part['intent'] < 2 * 86400:
            raise RuntimeError('云端结果文件尚未就绪，稍后重新下载') from exc
        raise
    for cid in part['custom_ids']:
        if cid not in rows:
            rows[cid] = {'custom_id': cid, 'error': {'message': f'MiMo 批次 {phase}，此候选未收到结果'}}
    _write(output, rows)
    part['done'] = True


def execute_batch(wb, pipeline, task, *, clock=time.time, client_factory=None):
    from bilive.analysis.batch_transport import BatchCredentialsError, MimoBatchClient
    path = _pending(wb, task)
    if not path.exists():
        return {'ok': True, 'already_finished': True}
    state = _read(path)
    folder = _folder(wb, task)
    rel = state['source_relpath']
    receipt = folder / 'receipt.json'
    if receipt.exists():
        finished = _read(receipt)
        if (finished.get('task') != task or finished.get('source_relpath') != rel
                or finished.get('phase') not in {'done', 'failed'}):
            raise ValueError('批量收据与当前场次不符')
        with wb.lock():
            _release_inputs(folder, path)
        return {'ok': finished['phase'] == 'done', 'already_finished': True}
    now = clock()
    try:
        enabled = _enabled(wb, state)
        parts = state.get('parts', [])
        attempted = any(p.get('intent') for p in parts)
        if not enabled and not attempted:
            _finish(wb, path, state, '主播已关闭夜间批量，本场未提交，可手动分析')
            return {'ok': True, 'cancelled': True}
        if state['phase'] == 'queued':
            _display(wb, state, 'preparing', '正在本机准备候选视频和批量请求')
            pipeline.prepare_batch(rel, task, folder)
            plan = _read(folder / 'analysis-plan.json')
            parts = [dict(p, output=f'result-{i}.json') for i, p in enumerate(plan['parts'])]
            for part in parts:
                name = part['file']
                file = (folder / name).resolve()
                if (name != file.name or file.parent != folder.resolve()
                        or not name.startswith('batch-input-') or not name.endswith('.jsonl')):
                    raise ValueError('批量输入不在本场准备目录内')
            state.update(phase='ready', parts=parts)
            _write(path, state)
            _display(wb, state, 'ready', '候选已准备好，等待北京时间 23:30 提交')

        if parts:
            client = None
            for part in parts:
                if part.get('done'):
                    continue
                enabled = _enabled(wb, state)
                if not part.get('intent') and not enabled:
                    part.update(done=True, error='主播已关闭夜间批量，此组未提交')
                    _write(path, state)
                    continue
                if not part.get('intent') and not _due_today(state, clock()):
                    continue
                try:
                    client = client or (client_factory or MimoBatchClient)()
                    _remote_part(client, path, state, part, folder,
                                 lambda: _due_today(state, clock()) and _enabled(wb, state), clock())
                    part.pop('last_error', None)
                except Exception as exc:
                    reason = redact(str(exc))[:450]
                    restore_access = part.get('intent') and (
                        isinstance(exc, BatchCredentialsError) or getattr(exc, 'status_code', None) in (401, 403))
                    if (_permanent(exc) and not restore_access) or now - part.get('intent', now) > 30 * 86400:
                        part.update(done=True, error=reason)
                    else:
                        part['last_error'] = reason
                _write(path, state)
            if not all(p.get('done') for p in parts):
                sent = sum(bool(p.get('batch_id')) for p in parts)
                attempted = any(p.get('intent') and not p.get('done') for p in parts)
                phase = 'submitted' if attempted else 'ready'
                message = (f'云端已确认 {sent}/{len(parts)} 个批次，将自动取回结果'
                           if attempted else '候选已准备好，等待北京时间 23:30 提交')
                err = next((p['last_error'] for p in parts if p.get('last_error')), None)
                if err:
                    message += '。稍后自动重查：' + err
                delay = (120 if err else POLL_SECONDS) if attempted or err else 0
                state.update(phase=phase, next_check=clock() + delay)
                _write(path, state)
                _display(wb, state, phase, message)
                return {'ok': True, 'phase': phase}

        rows = {}
        for part in parts:
            if part.get('error'):
                rows.update({cid: {'custom_id': cid, 'error': {'message': part['error']}}
                             for cid in part['custom_ids']})
            else:
                rows.update(_read(folder / part['output']))
        current = RecordingTasks(wb).history_for_write(wb.source(rel))
        if current.get('batch_stage') != 'completed':
            _display(wb, state, 'collecting', '正在将批量判断写回候选')
        result = pipeline.apply_batch(rel, task, folder, rows)
        all_failed = rows and all(row.get('error') for row in rows.values())
        message = None
        if all_failed:
            error = next(iter(rows.values()))['error']
            message = 'MiMo 批量判断均未成功：' + str(error.get('message', error) if isinstance(error, dict) else error)[:400]
        elif result.get('failed_candidates') and result['failed_candidates'] == result['candidates']:
            message = '所有候选的批量准备或判断都失败，请在分析证据中查看原因'
        _finish(wb, path, state, message)
        return result
    except (LockUnavailable, ActionConflict):
        raise  # A busy workbench does not consume this durable operation.
    except OSError as exc:
        if not isinstance(exc, FileNotFoundError):
            # Sharing violations/disk errors can interrupt any local checkpoint.
            # Retain the last durable file ID or creation intent for the next tick.
            logger.warning('Nightly batch checkpoint deferred for %s: %s', task, type(exc).__name__)
            raise
        _finish(wb, path, state, redact(str(exc))[:450])
        raise
    except Exception as exc:
        _finish(wb, path, state, redact(str(exc))[:450])
        raise
