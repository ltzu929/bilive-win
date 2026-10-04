"""Recording markers and explicit task controls, separate from source review."""
from __future__ import annotations
import json
import shutil
import time
from pathlib import Path
from bilive.domain.models import ActionJobStatus, task_id_from_source_relpath, source_relpath_from_task_id
from bilive.jobs.action_jobs import ActionConflict


class RecordingTasks:
    def __init__(self, workbench):
        self.wb = workbench

    def history_for_write(self, source):
        history=source.with_suffix('.mp4.task.json')
        if not history.is_file():return {}
        try:
            data=json.loads(history.read_text(encoding='utf-8'))
            if not isinstance(data,dict):raise ValueError('invalid history object')
            return data
        except (ValueError,UnicodeError):
            # Retain the unreadable evidence before an explicit task transition.
            task=task_id_from_source_relpath(source.relative_to(self.wb.videos).as_posix())
            backup=self.wb.artifacts/'recording-history'/task/'invalid-history.json'
            backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(history,backup)
            return {'prior_history_error':'invalid persisted history','prior_history_path':str(backup)}

    def status(self, task):
        rel = source_relpath_from_task_id(task)
        source = self.wb.source(rel)
        related = [(p,j) for state in ActionJobStatus for p,j in self.wb.jobs.valid_jobs(state)
                   if j.target == task and j.action == 'start_analysis']
        newest = max(related,key=lambda pair:pair[0].stat().st_mtime_ns)[1] if related else None
        markers = [s for s in ('pending','processing','done','failed') if source.with_suffix('.mp4.'+s).is_file()]
        return {'markers':markers, 'job':newest}

    def queue(self, task, automatic=False):
        from bilive.lifecycle.review import SourceReview
        source = self.wb.source(source_relpath_from_task_id(task))
        with self.wb.lock():
            record = SourceReview(self.wb).load(task)
            if record and record['review_state'] in {'review_complete','trash_pending'}:
                raise ActionConflict('source review already complete')
            job = self.wb.jobs.submit('start_analysis', task, {'automatic':True} if automatic else {})
            # Repeated active requests do not mutate the processing marker.
            if job.status == ActionJobStatus.PENDING:
                self.marker(source,'pending')
            SourceReview(self.wb).ensure(source.relative_to(self.wb.videos).as_posix())
            return job

    def marker(self, source, state):
        target = source.with_suffix('.mp4.' + state)
        # Write desired state first so a crash never produces a false terminal gap.
        temporary=target.with_suffix(target.suffix+'.tmp')
        temporary.write_text(json.dumps({'state':state,'updated_at':time.time()}),encoding='utf-8')
        temporary.replace(target)
        for other in ('pending','processing','done','failed'):
            if other != state:
                source.with_suffix('.mp4.'+other).unlink(missing_ok=True)

    def control(self, task, action):
        source = self.wb.source(source_relpath_from_task_id(task))
        with self.wb.lock():
            active = self.wb.jobs.find_active('start_analysis',task)
            if active and (active.action != 'start_analysis' or active.status == ActionJobStatus.PROCESSING):
                raise ActionConflict('recording task is active')
            if action == 'cancel':
                if active is None:
                    raise ActionConflict('no pending analysis to cancel')
                self.wb.jobs.cancel_pending(active.job_id)
                source.with_suffix('.mp4.pending').unlink(missing_ok=True)
                return {'cancelled':True,'job_id':active.job_id}
            if action == 'skip':
                if active:
                    self.wb.jobs.cancel_pending(active.job_id)
                history = source.with_suffix('.mp4.task.json')
                data = self.history_for_write(source)
                data.update(status='done', skipped=True, skip_reason='manual mark-done', requires_source_review=True)
                tmp = history.with_suffix('.json.tmp')
                tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');tmp.replace(history)
                self.marker(source,'done')
                return {'skipped':True,'review_complete':False}
            raise ValueError('unknown recording task control')

    def execute(self, job, pipeline):
        source = self.wb.source(source_relpath_from_task_id(job.target))
        with self.wb.lock():
            self.marker(source,'processing')
        try:
            if source.stat().st_size < pipeline.settings.video.min_source_size_mb * 1048576:
                history = {'source_relpath':source.relative_to(self.wb.videos).as_posix(), 'status':'done',
                           'skipped':True,'skip_reason':'below min_source_size_mb',
                           'candidates':[], 'segments':[], 'requires_source_review':True}
                pipeline._save_history(source.with_suffix('.mp4.task.json'),history)
                result = {'ok':True,'skipped':True, 'review_complete':False}
            else:
                result = pipeline.run(source.relative_to(self.wb.videos).as_posix(),job.target)
            with self.wb.lock():
                self.marker(source,'done')
            return result
        except Exception as exc:
            with self.wb.lock():
                history = source.with_suffix('.mp4.task.json')
                data = self.history_for_write(source)
                from bilive.config.secrets import redact
                data.update(status='failed', error=redact(str(exc)), requires_source_review=True)
                pipeline._save_history(history,data)
                self.marker(source,'failed')
            raise
