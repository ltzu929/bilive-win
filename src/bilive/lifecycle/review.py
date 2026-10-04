"""Independent source review records and resumable, bounded recycle manifests."""
from __future__ import annotations

import json
import time
from pathlib import Path

from bilive.domain.models import ActionJobStatus, task_id_from_source_relpath, source_relpath_from_task_id
from bilive.jobs.action_jobs import ActionConflict
from bilive.lifecycle.retention import DAY, _parse_time_from_name
from bilive.media.workbench import validate_id


class SourceReview:
    def __init__(self, workbench, mover=None):
        self.wb = workbench
        self.root = workbench.artifacts / "lifecycle"
        if mover is None:
            from bilive.lifecycle.recycle import move_to_recycle_bin
            mover = move_to_recycle_bin
        self.mover = mover

    def load(self, task: str) -> dict | None:
        path = self.root / f"{validate_id(task)}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def save(self, record: dict):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{validate_id(record['task_id'])}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def ensure(self, rel: str, now=None) -> dict:
        task = task_id_from_source_relpath(rel)
        record = self.load(task)
        if record:
            return record
        self.wb.source(rel)
        now = time.time() if now is None else now
        started = _parse_time_from_name(rel)
        started = now if started is None else started
        record = dict(task_id=task, source_relpath=rel, retention_started_at=started,
                      deadline=started + 7 * DAY, review_state="unprocessed",
                      trash_status=None, frozen_manifest=[], trash_results=[])
        self.save(record)
        return record

    def active(self, rel: str, current=None, forced=False):
        task = task_id_from_source_relpath(rel)
        sids = {s['segment_id'] for s in self.wb.list(rel)}
        for status in (ActionJobStatus.PENDING, ActionJobStatus.PROCESSING):
            for _, job in self.wb.jobs.valid_jobs(status):
                if job.job_id == current:
                    continue
                if forced:
                    blocked = job.target == task and job.action in {"trash_source", "force_expired"}
                else:
                    blocked = job.target in sids | {task}
                if blocked:
                    raise ActionConflict("source has an active task")

    def check_segments(self, rel: str):
        for s in self.wb.list(rel):
            if s.get("decision") == "drop":
                continue
            row = self.wb.uploads.get_by_path(s['final_path']) if s.get('final_path') else None
            published = row and row['stage'] == 'published' and row.get('bvid')
            if (s.get('final_revision') != s['revision'] or not s.get('publish_approval')
                    or not (published or self.wb.valid_final(s))):
                raise ActionConflict("all retained segments need a current approved final")

    def finish(self, task: str, no_content: bool = False, note: str = "") -> dict:
        validate_id(task)
        rel = source_relpath_from_task_id(task)
        with self.wb.lock():
            source = self.wb.source(rel)
            self.active(rel)
            segments = self.wb.list(rel)
            history_path = source.with_suffix('.mp4.task.json')
            history = json.loads(history_path.read_text(encoding='utf-8')) if history_path.is_file() else {}
            if history.get('status') in {'pending', 'processing'}:
                raise ActionConflict('source analysis is still active')
            if not segments and not no_content:
                raise ActionConflict('explicit no-content confirmation required')
            unresolved = [c for c in history.get('candidates', [])
                          if c.get('state') in {'review', 'failed', 'processing'} and not c.get('segments')]
            if unresolved and not no_content:
                raise ActionConflict('unresolved candidates require explicit no-content confirmation')
            if no_content and any(s.get('decision') != 'drop' for s in segments):
                raise ActionConflict('no-content conflicts with retained segments')
            self.check_segments(rel)
            from bilive.lifecycle.profiles import record_review
            record_review(self.wb, rel, task, segments, note)
            record = self.ensure(rel)
            record.update(review_state='review_complete', review_result='no_content' if no_content else 'complete',
                          review_note=note, reviewed_at=time.time())
            self.save(record)
            source.with_suffix('.mp4.done').touch()
            try:
                job = self.wb.jobs.submit('trash_source', task, {})
            except Exception as exc:
                return dict(review_saved=True, recycle_queued=False, detail=type(exc).__name__, record=record)
            record.update(review_state='trash_pending', trash_status='pending', trash_job_id=job.job_id)
            self.save(record)
            return dict(review_saved=True, recycle_queued=True, job_id=job.job_id, record=record)

    def queue(self, task: str, forced=False, now=None):
        rel = source_relpath_from_task_id(validate_id(task))
        with self.wb.lock():
            record = self.ensure(rel, now)
            if record['trash_status'] == 'done':
                return dict(queued=False, reason='already_recycled')
            if forced:
                if (time.time() if now is None else now) < record['deadline']:
                    raise ActionConflict('source retention has not expired')
            elif record['review_state'] not in {'review_complete', 'trash_pending'}:
                raise ActionConflict('review incomplete')
            self.active(rel, forced=forced)
            job = self.wb.jobs.submit('force_expired' if forced else 'trash_source', task, {},
                                      expired_retention=forced)
            record.update(review_state='trash_pending', trash_status='pending', trash_job_id=job.job_id)
            self.save(record)
            return dict(queued=True, job_id=job.job_id)

    def maintenance(self, now=None):
        now = time.time() if now is None else now
        results = []
        for source in sorted(self.wb.videos.rglob('*.mp4')):
            rel = source.relative_to(self.wb.videos).as_posix()
            with self.wb.lock():
                record = self.ensure(rel, now)
                if now >= record['retention_started_at'] + 5 * DAY and record['trash_status'] != 'done':
                    record.setdefault('retention_warned_at', now)
                    self.save(record)
            result = dict(task_id=record['task_id'], warn_day5=now >= record['retention_started_at'] + 5 * DAY,
                          expired_day7=now >= record['deadline'])
            if result['expired_day7']:
                try:
                    result.update(self.queue(record['task_id'], forced=True, now=now))
                except ActionConflict as exc:
                    result.update(queued=False, reason=str(exc))
            results.append(result)
        return results  # Deliberately never wakes or drains the Worker.

    def protected(self):
        paths = set()
        for file in self.wb.root.glob('*.json'):
            s = self.wb.load(file.stem)
            for value in [s.get('final_path'), s.get('generated_path'), *s.get('artifact_history', [])]:
                if value:
                    paths.add(Path(value).resolve())
        return paths

    def checked(self, entry, protected, record=None):
        roots = {'videos': self.wb.videos, 'candidates': self.wb.artifacts / 'candidates',
                 'raw_segments': self.wb.artifacts}
        root = roots.get(entry.get('root'))
        if root is None:
            raise ValueError('invalid manifest root')
        rel = Path(entry['relpath'])
        path = (root / rel).resolve()
        if (rel.is_absolute() or not path.is_relative_to(root.resolve()) or path in protected
                or any(part in {'final_output', 'final_artifacts', 'output_slices'} for part in rel.parts)):
            raise ValueError('manifest escapes root or includes protected final')
        if record:
            if task_id_from_source_relpath(record['source_relpath']) != record['task_id']:
                raise ValueError('lifecycle source identity mismatch')
            source = (self.wb.videos / record['source_relpath']).resolve()
            if not source.is_relative_to(self.wb.videos):
                raise ValueError('lifecycle source escapes root')
            if entry['root'] == 'raw_segments':
                sids = {s['segment_id'] for s in self.wb.list(record['source_relpath'])}
                if (len(rel.parts) != 2 or rel.parts[0] not in sids or rel.parts[1] not in
                        {'raw_candidate.mp4', 'raw_candidate.range.json', 'raw_next.mp4',
                         'asr_window.mp4', 'asr_window.range.json', 'asr_next.mp4'}):
                    raise ValueError('raw file outside source package')
            elif entry['root'] == 'candidates':
                if not path.is_relative_to((root / record['task_id']).resolve()):
                    raise ValueError('candidate outside source package')
            elif (path.parent != source.parent or path.name not in
                  {source.with_suffix(s).name for s in ('.mp4', '.flv', '.xml', '.flv.meta',
                   '.flv.meta.json', '.json', '.ass', '.mp4.task.json', '.mp4.pending',
                   '.mp4.processing', '.mp4.failed', '.mp4.done')}):
                raise ValueError('file outside source package')
        return path

    def manifest(self, rel, task):
        source = self.wb.source(rel)
        paths = [source, source.with_suffix('.flv')]
        paths += [source.with_suffix(suffix) for suffix in
                  ('.xml', '.flv.meta', '.flv.meta.json', '.json', '.ass', '.mp4.task.json',
                   '.mp4.pending', '.mp4.processing', '.mp4.failed', '.mp4.done')]
        candidate_root = self.wb.artifacts / 'candidates'
        package = candidate_root / task
        paths += [p for p in package.rglob('*') if p.is_file()]
        entries = []
        for p in paths:
            if p.is_file():
                root = self.wb.videos if p.is_relative_to(self.wb.videos) else candidate_root
                entries.append(dict(root='videos' if root == self.wb.videos else 'candidates',
                                    relpath=p.relative_to(root).as_posix()))
        for segment in self.wb.list(rel):
            for name in ('raw_candidate.mp4', 'raw_candidate.range.json', 'raw_next.mp4',
                         'asr_window.mp4', 'asr_window.range.json', 'asr_next.mp4'):
                raw = self.wb.artifacts / segment['segment_id'] / name
                if raw.is_file():
                    entries.append(dict(root='raw_segments', relpath=raw.relative_to(self.wb.artifacts).as_posix()))
        return entries

    def execute(self, job):
        with self.wb.lock():
            record = self.load(job.target)
            if record is None:
                raise ValueError('lifecycle record missing')
            if record['trash_status'] == 'done':
                return dict(ok=True, already_recycled=True)
            forced = job.action == 'force_expired'
            if forced and time.time() < record['deadline']:
                raise ActionConflict('source retention has not expired')
            rel = record['source_relpath']
            self.active(rel, job.job_id, forced)
            if not forced:
                if record['review_state'] not in {'review_complete', 'trash_pending'}:
                    raise ActionConflict('review incomplete')
                self.check_segments(rel)
                for s in self.wb.list(rel):
                    row = self.wb.uploads.get_by_path(s['final_path']) if s.get('final_path') else None
                    if row and row['stage'] in {'uploading', 'publishing'}:
                        raise ActionConflict('upload is active')
            protected = self.protected()
            if not record['frozen_manifest']:
                record['frozen_manifest'] = self.manifest(rel, job.target)
                # Validate the entire package before moving any file.
                for entry in record['frozen_manifest']:
                    self.checked(entry, protected, record)
                self.save(record)
            try:
                for entry in record['frozen_manifest']:
                    self.checked(entry, protected, record)
            except ValueError as exc:
                record.update(trash_status='blocked', trash_error=str(exc))
                self.save(record)
                raise
            record['trash_status'] = 'processing'
            self.save(record)
            successes = {r['index'] for r in record['trash_results'] if r.get('ok')}
            for index, entry in enumerate(record['frozen_manifest']):
                try:
                    path = self.checked(entry, protected, record)
                    if index in successes:
                        if path.exists():
                            raise ValueError('confirmed recycled path exists again')
                        continue
                    if not path.is_file():
                        raise FileNotFoundError('unknown move result; frozen item is missing')
                    self.mover([path])
                    if path.exists():
                        raise OSError('recycle operation did not remove source path')
                    record['trash_results'].append(dict(index=index, ok=True))
                    self.save(record)
                except Exception as exc:
                    record.update(trash_status='failed', trash_error=type(exc).__name__ + ': ' + str(exc))
                    self.save(record)
                    raise
            record.update(trash_status='done', trash_error=None)
            self.save(record)
            return dict(ok=True, trash_status='done', moved=len(record['trash_results']))
