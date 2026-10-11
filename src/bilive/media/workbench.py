"""Persistent manual review shared by the light API and Worker."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from bilive.domain.models import ActionJobStatus, SubtitleLine, clamp_subtitle_times, segment_id_from_source_and_range
from bilive.jobs.action_jobs import ActionConflict, ActionJobStore
from bilive.media.finalize import Finalizer, PipelineError
from bilive.media.subtitle_store import SubtitleStore
from bilive.upload.queue import UploadQueue
from bilive.worker.locks import ProcessLock
from bilive.worker.timing import timed_stage


def validate_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,240}", value):
        raise ValueError("invalid id")
    return value


class Workbench:
    def __init__(self, videos: Path, artifacts: Path, jobs: ActionJobStore,
                 subtitles: SubtitleStore, uploads: UploadQueue, default_style: dict | None = None):
        self.default_style = dict(default_style or {'font_name':'Noto Sans SC','font_size':20,'margin_v':60})
        self.default_style_provider = None
        self.settings_provider = None
        self.recording_active_provider = None
        self.videos = videos.resolve()
        self.artifacts = artifacts.resolve()
        self.root = self.artifacts / "segments"
        self.jobs, self.subtitles, self.uploads = jobs, subtitles, uploads

    def lock(self) -> ProcessLock:
        return ProcessLock(self.artifacts / "workbench.lock")

    def segment_lock(self, sid: str) -> ProcessLock:
        return ProcessLock(self.artifacts / 'segment-locks' / f'{validate_id(sid)}.lock')

    def current_default_style(self) -> dict:
        return dict(self.default_style_provider() if self.default_style_provider else self.default_style)

    def source(self, rel: str) -> Path:
        path = (self.videos / rel).resolve()
        if Path(rel).is_absolute() or not path.is_relative_to(self.videos):
            raise ValueError("source path escapes videos root")
        if not path.is_file() or path.suffix.lower() != ".mp4":
            raise FileNotFoundError("source MP4 missing")
        return path

    def load(self, sid: str) -> dict:
        path = self.root / f"{validate_id(sid)}.json"
        if not path.is_file():
            raise FileNotFoundError("segment missing")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, segment: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{validate_id(segment['segment_id'])}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(segment, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def list(self, source: str) -> list[dict]:
        return [s for p in sorted(self.root.glob("*.json"))
                if (s := self.load(p.stem))["source_relpath"] == source]

    def active(self, sid: str) -> dict | None:
        for status in ("pending", "processing"):
            for p, job in self.jobs.valid_jobs(ActionJobStatus(status)):
                if job.target == sid:
                    return {"job_id": job.job_id, "action": job.action, "status": status}
        return None

    def editable(self, s: dict, *, allow_source_analysis: bool = False) -> None:
        if s.get('state') == 'dropped':
            raise ActionConflict('segment has been discarded')
        from bilive.lifecycle.review import SourceReview
        from bilive.domain.models import task_id_from_source_relpath
        record = SourceReview(self).load(task_id_from_source_relpath(s['source_relpath']))
        if record and record['review_state'] in {'review_complete', 'trash_pending'}:
            raise ActionConflict('source review already complete')
        if self.active(s["segment_id"]):
            raise ActionConflict("segment has an active task")
        self.check_source_idle(s['source_relpath'], allow_analysis=allow_source_analysis)
        if s.get("publish_approval"):
            raise ActionConflict("approved final is protected; create a new manual segment")

    def check_source_idle(self, rel: str, *, allow_analysis: bool = False):
        from bilive.domain.models import task_id_from_source_relpath
        task = task_id_from_source_relpath(rel)
        if any(j.target == task and not (allow_analysis and j.action == 'start_analysis')
               for state in (ActionJobStatus.PENDING,ActionJobStatus.PROCESSING)
               for _,j in self.jobs.valid_jobs(state)):
            raise ActionConflict('source has an active analysis or recycle action')

    def invalidate(self, s: dict) -> None:
        s.pop('preview', None)
        s.pop('cover_path', None)
        if s.get("final_path"):
            if s.get('publish_approval'):
                raise ActionConflict('approved final is protected')
            # A previous interrupted withdrawal may already have removed the
            # staged row before the segment file was saved. Keep its media and
            # finish invalidating; an existing protected row still fails atomically.
            if self.uploads.get_by_path(s['final_path']) is not None:
                try:
                    self.uploads.remove_staged(s["final_path"])
                except ValueError as exc:
                    raise ActionConflict(str(exc)) from None
            s.setdefault("artifact_history", []).append(s["final_path"])
            s.update(final_path=None, final_revision=None)
        s["state"] = "needs_finalize"

    def points(self, rel: str, start: float, end: float, title: str,
               note: str, sid: str | None = None, expected: int | None = None) -> dict:
        self.source(rel)
        if not all(math.isfinite(x) for x in (start, end)) or start < 0 or end <= start:
            raise ValueError("invalid source range")
        if not note.strip():
            raise ValueError("manual selection reason required")
        with self.lock():
            from bilive.lifecycle.review import SourceReview
            self.check_source_idle(rel)
            from bilive.domain.models import task_id_from_source_relpath
            record = SourceReview(self).load(task_id_from_source_relpath(rel))
            if record and record['review_state'] in {'review_complete', 'trash_pending'}:
                raise ActionConflict('source review already complete')
            if sid:
                s = self.load(sid)
                self.check_revision(s, expected)
                self.editable(s)
                if s["source_relpath"] != rel:
                    raise ValueError("cannot change segment source")
                self.invalidate(s)
                s["revision"] += 1
                s["subtitles_revision"] = None
                s['human_reviewed'] = False
            else:
                sid = segment_id_from_source_and_range(rel, round(start * 1000), round(end * 1000))
                validate_id(sid)
                if (self.root / f"{sid}.json").is_file():
                    return self.load(sid)
                s = {"segment_id": sid, "source_relpath": rel, "revision": 1,
                     "state": "needs_finalize", "decision": "manual_keep",
                     "final_path": None, "final_revision": None,
                     "subtitles_revision": None, "publish_approval": False}
                from bilive.lifecycle.profiles import profile_for_source
                profile = profile_for_source(self.videos, rel)
                s.update(subtitle_style={**self.current_default_style(), **profile.get('default_subtitle_style', {})},
                         description=profile.get('default_description', ''), tags=profile.get('default_tags', []))
                from bilive.media.publication import defaults_for_segment
                s.update(defaults_for_segment(self, rel, title))
                title = s['title']
            s.update(source_start=start, source_end=end, title=title, note=note)
            self.save(s)
            return s

    @staticmethod
    def check_revision(s: dict, expected: int | None) -> None:
        if expected != s["revision"]:
            raise ActionConflict("revision conflict; reload the segment and keep your draft")

    def keep(self, sid: str, expected: int) -> dict:
        """Confirm the editorial decision without changing the media revision."""
        with self.lock():
            s = self.load(sid)
            self.check_revision(s, expected)
            self.editable(s)
            s['technical_failure'] = bool(s.get('technical_failure') or s.get('processing_error') or s['decision'] == 'judge_failed')
            s.update(decision='manual_keep', human_reviewed=True)
            if s.get('state') == 'needs_review':
                s['state'] = 'awaiting_publish' if self.valid_final(s) else 'needs_finalize'
            self.save(s)
            return self.detail(sid)

    def save_metadata(self, sid: str, expected: int, title: str, description: str, tags: list[str], tid: int | None = None) -> dict:
        title = title.strip()
        tags = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        if not title or len(title) > 80:
            raise ValueError('标题须为 1–80 个字符')
        if len(description) > 2000:
            raise ValueError('简介不能超过 2000 个字符')
        if len(tags) > 10 or any(len(tag) > 20 or re.search(r'[,，\n]', tag) for tag in tags):
            raise ValueError('TAG 最多 10 个，每个不超过 20 个字符，不能包含逗号或换行')
        from bilive.media.publication import validate_metadata
        validate_metadata(title, description, tags, tid)
        with self.lock():
            s = self.load(sid)
            self.check_revision(s, expected)
            self.editable(s)
            # Publication metadata does not change the video or subtitle text.
            if s.get('final_path'):
                row = self.uploads.get_by_path(s['final_path'])
                if row is None:
                    raise ActionConflict('final queue state is unknown')
                try:
                    self.uploads.stage(s['final_path'], title, description, tags,
                                       tid=tid if tid is not None else s.get('tid'), cover_path=s.get('cover_path'))
                except ValueError as exc:
                    raise ActionConflict(str(exc)) from None
            saved = self.subtitles.load(sid)
            s.update(title=title, description=description, tags=tags, revision=expected+1)
            if tid is not None:
                s['tid'] = tid
            if s.get('preview', {}).get('revision') == expected:
                s['preview']['revision'] = s['revision']
            if s.get('subtitles_revision') == expected:
                lines = [SubtitleLine(v['index'], v['start'], v['end'], v['text']) for v in saved['lines']]
                self.subtitles.save(sid, s['source_end']-s['source_start'], lines, s['revision'])
                s['subtitles_revision'] = s['revision']
            if s.get('final_revision') == expected:
                s['final_revision'] = s['revision']
            self.save(s)
            return self.detail(sid)

    def save_subtitles(self, sid: str, expected: int, lines: list[SubtitleLine]) -> dict:
        # AI owns the same segment lock until its final save. Other completed
        # segments can be edited while source analysis prepares later results.
        with self.segment_lock(sid), self.lock():
            s = self.load(sid)
            self.check_revision(s, expected)
            self.editable(s, allow_source_analysis=True)
            duration = s["source_end"] - s["source_start"]
            if not lines:
                raise ValueError("at least one subtitle line required")
            clamp_subtitle_times(lines, duration)
            self.invalidate(s)
            s["revision"] += 1
            self.subtitles.save(sid, duration, lines, s["revision"])
            s["subtitles_revision"] = s["revision"]
            self.save(s)
            return s

    def save_style(self, sid: str, expected: int, appearance: dict) -> dict:
        from bilive.vendor.subtitle_style import validate_style, SubtitleStyle
        validate_style(appearance)
        normalized = SubtitleStyle.from_mapping({**self.current_default_style(), **appearance}).to_mapping()
        with self.lock():
            s = self.load(sid)
            self.check_revision(s, expected)
            self.editable(s)
            saved = self.subtitles.load(sid)
            self.invalidate(s)
            s.update(revision=s['revision']+1, subtitle_style=normalized)
            if saved['revision'] == expected and saved['lines']:
                lines = [SubtitleLine(v['index'],v['start'],v['end'],v['text']) for v in saved['lines']]
                self.subtitles.save(sid,s['source_end']-s['source_start'],lines,s['revision'])
                s['subtitles_revision'] = s['revision']
            self.save(s)
            return s

    def submit(self, action: str, sid: str, expected: int) -> dict:
        if action not in {"finalize", "reburn", "render", "approve_publish", "retry_judge", "transcribe_subtitles", "generate_metadata"}:
            raise ValueError("action not available in manual workbench")
        with self.lock():
            s = self.load(sid)
            self.check_revision(s, expected)
            from bilive.lifecycle.review import SourceReview
            if s.get('state') == 'dropped':
                raise ActionConflict('segment has been discarded')
            self.check_source_idle(s['source_relpath'])
            from bilive.domain.models import task_id_from_source_relpath
            record = SourceReview(self).load(task_id_from_source_relpath(s['source_relpath']))
            if record and record['review_state'] in {'review_complete', 'trash_pending'}:
                raise ActionConflict('source review already complete')
            active = self.active(sid)
            if active and active["action"] != action:
                raise ActionConflict("conflicting active action")
            if action != "approve_publish" and s.get("publish_approval"):
                raise ActionConflict("approved final is protected")
            if action == "approve_publish" and not self.valid_final(s):
                raise ActionConflict("current revision has no valid final")
            if action == 'retry_judge' and not s.get('candidate_path'):
                raise ActionConflict('manual segment has no automatic candidate to rejudge')
            if action not in {"approve_publish", "retry_judge", "transcribe_subtitles"} and s.get("subtitles_revision") != expected:
                raise ActionConflict("save subtitles for this revision first")
            if action == 'generate_metadata':
                saved = self.subtitles.load(sid)
                if saved['revision'] != expected or not saved['lines']:
                    raise ActionConflict('请先保存当前片段的字幕，再生成文案建议')
            payload = {"revision": expected, "source_relpath": s["source_relpath"],
                       "source_start": s["source_start"], "source_end": s["source_end"]}
            job = self.jobs.submit(action, sid, payload)
            s["last_job_id"] = job.job_id
            self.save(s)
            return {"job_id": job.job_id, "status": job.status.value, "action": action}

    def valid_final(self, s: dict) -> bool:
        if not (s.get("final_path") and s.get("final_revision") == s["revision"]
                and self.subtitles.load(s["segment_id"])["revision"] == s["revision"]
                and Path(s["final_path"]).is_file()):
            return False
        row = self.uploads.get_by_path(s["final_path"])
        return bool(row)

    def drop(self, sid: str, expected: int, reason: str) -> dict:
        if not reason.strip():
            raise ValueError('drop reason required')
        with self.lock():
            s = self.load(sid)
            self.check_revision(s, expected)
            self.editable(s)
            s['technical_failure'] = bool(s.get('technical_failure') or s.get('processing_error') or s['decision'] == 'judge_failed')
            self.invalidate(s)
            s.update(decision='drop', state='dropped', drop_reason=reason, revision=s['revision'] + 1)
            self.save(s)
            return s

    def detail(self, sid: str) -> dict:
        s = self.load(sid)
        s["subtitles"] = (self.subtitles.load(sid)["lines"]
                          if s.get("subtitles_revision") == s["revision"] else [])
        s["final_valid"] = self.valid_final(s)
        s["active_job"] = self.active(sid)
        s['preview_complete'] = bool(s.get('final_valid') and s.get('preview', {}).get('complete') and
                                     s.get('preview', {}).get('revision') == s.get('final_revision'))
        if s.get('last_job_id'):
            for status in ActionJobStatus:
                path = self.jobs.root / f"{s['last_job_id']}.{status.value}.json"
                if path.is_file():
                    job = self.jobs.load_job(path)
                    s['last_job'] = {'job_id': job.job_id, 'action': job.action,
                                     'status': status.value, 'revision': job.payload.get('revision'),
                                     'error': (job.result or {}).get('error'), 'progress': job.progress}
                    if job.action == 'transcribe_subtitles':
                        s['transcription_job'] = {'status': status.value, 'payload': job.payload, 'result': job.result}
                    elif job.action == 'generate_metadata':
                        s['metadata_job'] = {'status': status.value, 'payload': job.payload, 'result': job.result}
                    break
        return s

    def record_preview(self, sid: str, expected: int, ranges: list[list[float]]) -> dict:
        """Accumulate played intervals for this final only; never authorize upload."""
        with self.lock():
            s = self.load(sid)
            self.check_revision(s, expected)
            if not self.valid_final(s):
                raise ActionConflict('没有当前版本的可预览成片')
            duration = s['source_end'] - s['source_start']
            if len(ranges) > 200:
                raise ValueError('预览区间过多')
            previous = s.get('preview', {})
            intervals = list(previous.get('ranges', [])) if previous.get('revision') == expected else []
            for pair in ranges:
                if (len(pair) != 2 or not all(math.isfinite(v) for v in pair) or
                        pair[0] < 0 or pair[1] <= pair[0] or pair[1] > duration + .5):
                    raise ValueError('预览区间无效')
                intervals.append([pair[0], min(pair[1], duration)])
            merged = []
            for start, end in sorted(intervals):
                if merged and start <= merged[-1][1] + .25:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            if len(merged) > 200:
                raise ValueError('预览区间过多')
            complete = len(merged) == 1 and merged[0][0] <= .25 and merged[0][1] >= duration - .25
            s['preview'] = {'revision': expected, 'ranges': merged, 'complete': complete}
            self.save(s)
            return s['preview']

    def execute(self, job, finalizer: Finalizer) -> dict:
        with self.lock():
            s = self.load(job.target)
            self.check_revision(s, job.payload["revision"])
            if s.get('state') == 'dropped':
                raise PipelineError('segment has been discarded')
            if job.action == "approve_publish":
                row = self.uploads.get_by_path(s['final_path']) if s.get('final_path') else None
                recovering = (s.get('approval_job_id') == job.job_id
                              and s.get('approval_revision') == s['revision'])
                if recovering and row and row['stage'] in {'queued','uploading','uploaded','publishing','published','failed'}:
                    # This exact validated job activated the queue before a crash.
                    # Never activate it twice or require a cleaned published file.
                    s.update(publish_approval=True,state=row['stage'])
                    self.save(s)
                    return {'ok':True,'upload_stage':row['stage'],'recovered_activation':True}
                if not self.valid_final(s):
                    raise PipelineError("no current final output")
                s.update(approval_job_id=job.job_id,approval_revision=s['revision'])
                self.save(s)
                row = self.uploads.activate(s["final_path"])
                s.update(publish_approval=True, state=row["stage"])
                self.save(s)
                return {"ok": True, "upload_stage": row["stage"]}
            if s.get("publish_approval"):
                raise PipelineError("approved final is protected")
            if s.get("subtitles_revision") != s["revision"]:
                raise PipelineError("subtitles do not match current revision")
            if not s["title"].strip():
                raise PipelineError("upload title required; edit the segment title")
            self.invalidate(s)
            self.save(s)
            try:
                with timed_stage(s,'manual_finalize'):
                    result = finalizer.reburn(s["segment_id"], s["source_relpath"],
                                              s["source_start"], s["source_end"], s["revision"])
            except Exception:
                self.save(s)
                raise
            # A failed queue write keeps the media but cannot authorize it.
            s.update(state="staging_failed", final_path=None, final_revision=None,
                     generated_path=result.final_path)
            self.save(s)
            try:
                with timed_stage(s,'stage'):
                    from bilive.media.publication import generate_default_cover
                    s['cover_path'] = str(generate_default_cover(Path(result.final_path)))
                    self.uploads.stage(result.final_path, title=s["title"], description=s.get('description', ''),
                                       tags=s.get('tags', []), tid=s.get('tid'), cover_path=s['cover_path'])
            except Exception:
                self.save(s)
                raise
            s.update(state="awaiting_publish", final_path=result.final_path,
                     final_revision=s["revision"], decision='manual_keep')
            self.save(s)
            return {"ok": True, "final_path": result.final_path, "revision": s["revision"],
                    "upload_stage": "staged"}
