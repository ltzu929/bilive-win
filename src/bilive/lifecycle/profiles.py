"""Adapt pinned human evidence rules to current workbench records."""
from __future__ import annotations
import re
from pathlib import Path
from bilive.vendor import streamer_profiles as store


def room_from_source(rel: str) -> str | None:
    path = Path(rel)
    for text in (path.parent.name, path.name):
        match = re.match(r'^(\d+)(?:\s*-|_|$)', text)
        if match:
            return match[1]
    match = re.match(r'blive_(\d+)_', path.name)
    if match:
        return match[1]
    return None


def profile_for_source(videos: Path, rel: str) -> dict:
    room = room_from_source(rel)
    return store.read_streamer_profile(videos, room) if room else {}


def record_review(wb, rel: str, task: str, segments: list[dict], note: str):
    room = room_from_source(rel)
    if not room:
        return []
    records = []
    for s in segments:
        technical = bool(s.get('processing_error') or s.get('technical_failure') or s['decision'] == 'judge_failed')
        kind = ('technical_failure' if technical else
                'negative' if s['decision'] == 'drop' else
                'missed_segment_positive' if s['decision'] == 'manual_keep' else 'positive')
        transcript = ' '.join(v['text'] for v in wb.subtitles.load(s['segment_id'])['lines'])[:1000]
        # Evidence remains available after the source package is recycled.
        texts = []
        xml = wb.source(rel).with_suffix('.xml')
        if xml.is_file():
            from bilive.analysis.pipeline import read_danmaku
            texts = [text for ts, text in read_danmaku(xml) if s['source_start'] <= ts <= s['source_end']]
        records.append(store.append_experience(wb.videos, {
            'room_id':room, 'task_id':task, 'source_rel_path':rel, 'segment_id':s['segment_id'],
            'experience_type':kind, 'conclusion':'technical_failure' if technical else ('negative' if kind == 'negative' else 'positive'),
            'reason_type':'technical_failure' if technical else str(s.get('missed_reason') or s.get('drop_reason') or s.get('note') or 'content_review')[:100],
            'note':s.get('drop_reason') or s.get('note') or note,
            'model_judgment':s['decision'], 'model_scores':{k:s.get(k) for k in ('quality_score','completeness_score','confidence')},
            'start_seconds':s['source_start'], 'end_seconds':s['source_end'],
            'transcript_summary':transcript, 'danmaku_summary':{'count':len(texts),'text':' '.join(texts)[:1000]},
            'dedupe_key':f"{room}:{task}:{s['segment_id']}:{s['revision']}:{kind}",
        }))
    if not segments:
        records.append(store.append_experience(wb.videos, {'room_id':room,'task_id':task,
            'source_rel_path':rel,'experience_type':'recording_no_content','conclusion':'no_content',
            'note':note,'dedupe_key':f'{room}:{task}:recording_no_content'}))
    store.generate_streamer_recommendation(wb.videos, room)
    return records
