"""Expire local clip outputs without removing subtitles or publication evidence."""
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import time

from bilive.domain.models import task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionConflict
from bilive.lifecycle.retention import DAY
from bilive.lifecycle.review import SourceReview


def _timestamp(value):
    if value is None:
        return None
    if isinstance(value, str):
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('retention timestamp needs a timezone')
        value = stamp.timestamp()
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('invalid retention timestamp')
    return value


def cleanup_clips(workbench, now=None):
    """Published clips use publication time; unpublished outputs use file age."""
    now = time.time() if now is None else now
    lifecycle = SourceReview(workbench)
    results = []
    with workbench.lock():
        policy = lifecycle.policy()
        if not policy.enabled:
            return results
        conn = workbench.uploads._conn(readonly=True)
        try:
            rows, ambiguous = {}, set()
            for row in conn.execute('SELECT * FROM upload_queue'):
                path = Path(row['video_path']).resolve()
                if path in rows:
                    ambiguous.add(path)
                rows[path] = dict(row)
        finally:
            conn.close()
        for file in sorted(workbench.root.glob('*.json')):
            segment = workbench.load(file.stem)
            paths = {Path(value) for value in
                     (segment.get('final_path'), segment.get('generated_path')) if value}
            for original in paths:
                path = original.resolve()
                folder = (workbench.artifacts / file.stem).resolve()
                if (segment['segment_id'] != file.stem or folder.parent != workbench.artifacts or
                        path in ambiguous or original.is_symlink() or path.parent != folder or
                        path.name != 'final_output.mp4' or not path.is_file()):
                    continue
                row = rows.get(path)
                if row and row['stage'] not in {'published', 'staged', 'cancelled', 'failed'}:
                    continue
                try:
                    if row and row['stage'] == 'published':
                        if not row.get('bvid'):
                            continue
                        stamp = _timestamp(row.get('published_at'))
                        if stamp is None:
                            stamp = _timestamp(row['updated_at'])
                        deadline = stamp + policy.published_hours * 3600
                    else:
                        if row and row.get('bvid'):
                            continue
                        # A replaced output starts a new review window.
                        deadline = path.stat().st_mtime + policy.unprocessed_days * DAY
                    if now < deadline:
                        continue
                    record = lifecycle.load(task_id_from_source_relpath(segment['source_relpath'])) or {}
                    if record.get('manual_keep'):
                        continue
                    lifecycle.active(segment['source_relpath'])
                    path.unlink()
                    results.append({'kind': 'clip', 'path': str(path),
                                    'deadline': deadline, 'deleted': True})
                except ActionConflict:
                    continue
                except (OSError, ValueError) as exc:
                    results.append({'kind': 'clip', 'path': str(path),
                                    'deleted': False, 'error': type(exc).__name__})
    return results
