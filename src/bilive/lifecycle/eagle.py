"""Read-only source snapshot adapted from pinned dashboard/eagle_index.py."""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from bilive.domain.models import task_id_from_source_relpath
from bilive.lifecycle.retention import _parse_time_from_name
from bilive.lifecycle.profiles import room_from_source, profile_for_source


def source_index(wb):
    if not wb.videos.is_dir():
        raise FileNotFoundError('source root unavailable; Eagle synchronization stopped')
    items = []
    for file in sorted(wb.videos.rglob('*.mp4')):
        rel = file.relative_to(wb.videos).as_posix()
        source = wb.source(rel)
        segments = wb.list(rel)
        history_path = source.with_suffix('.mp4.task.json')
        history = json.loads(history_path.read_text(encoding='utf-8')) if history_path.is_file() else {}
        room = room_from_source(rel) or ''
        profile = profile_for_source(wb.videos, rel)
        task = task_id_from_source_relpath(rel)
        epoch = _parse_time_from_name(rel)
        items.append({
            'source_task_id':task, 'source_rel_path':rel, 'source_name':source.name,
            'room_id':room, 'room_name':profile.get('display_name') or room or source.parent.name,
            'recorded_at':datetime.fromtimestamp(epoch, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') if epoch is not None else '',
            'source_size_mb':source.stat().st_size / 1048576, 'status':str(history.get('status') or 'unprocessed'),
            'segment_count':len(segments),
            'review_count':sum(s['decision'] in {'review','judge_failed'} or bool(s.get('processing_error')) for s in segments),
            'keep_count':sum(s['decision'] in {'keep','manual_keep'} for s in segments),
            'thumbnail_url':'',
            'workspace_url':f'/workbench/?source_task_id={task}#/library',
        })
    return items
