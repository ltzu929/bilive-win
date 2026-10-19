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


def subtitle_hotwords_for_profile(profile: dict) -> list[str]:
    """Reuse confirmed profile names without learning from generated subtitles."""
    names = [profile.get('display_name', ''), *profile.get('aliases', []),
             *profile.get('subtitle_hotwords', [])]
    return list(dict.fromkeys(name.strip() for name in names
                             if isinstance(name, str) and name.strip()))


def record_review(wb, rel: str, task: str, segments: list[dict], note: str):
    room = room_from_source(rel)
    if not room:
        return []
    records = []
    for s in segments:
        human = s.get('human_feedback', {})
        human_reason = human.get('reason', '') if 'human_feedback' in s else s.get('drop_reason') or s.get('missed_reason') or ''
        technical = bool(human.get('kind') == 'technical' or s.get('processing_error') or s.get('technical_failure') or s['decision'] == 'judge_failed')
        duplicate = s['decision'] == 'drop' and (human.get('kind') == 'duplicate' or store.is_duplicate_experience(s))
        kind = ('technical_failure' if technical else
                'duplicate' if duplicate else
                'uncertain' if human.get('uncertain') else
                'negative' if s['decision'] == 'drop' else
                'missed_segment_positive' if s.get('missed_reason') else 'positive')
        edits = s.get('editorial_history', [])
        initial = edits[0].get('before', {}) if edits else s
        transcript = ' '.join(v['text'] for v in wb.subtitles.load(s['segment_id'])['lines'])[:1000]
        # Evidence remains available after the source package is recycled.
        texts = []
        xml = wb.source(rel).with_suffix('.xml')
        if xml.is_file():
            from bilive.analysis.pipeline import read_danmaku
            texts = [text for ts, text in read_danmaku(xml) if s['source_start'] <= ts <= s['source_end']]
        records.append(store.append_experience(wb.videos, {
            'room_id':room, 'task_id':task, 'source_rel_path':rel, 'segment_id':s['segment_id'],
            'title':s.get('title', ''),
            'experience_type':kind, 'conclusion':kind if kind in {'technical_failure', 'duplicate', 'negative', 'uncertain'} else 'positive',
            'reason_type':'technical_failure' if technical else str(human_reason or 'content_review')[:100],
            'note':human_reason, 'original_note':s.get('note', ''),
            'human_reason':human_reason, 'human_feedback':human,
            'human_decision':s['decision'], 'selection_origin':s.get('selection_origin', 'unknown'),
            'editorial_history':edits,
            'duplicate_of':s.get('duplicate_of'),
            'model_judgment':initial.get('decision') if initial.get('decision') != 'manual_keep' else None,
            'model_scores':{k:initial.get(k) for k in ('quality_score','completeness_score','confidence')},
            'start_seconds':s['source_start'], 'end_seconds':s['source_end'],
            'transcript_summary':transcript, 'danmaku_summary':{'count':len(texts),'text':' '.join(texts)[:1000]},
            'dedupe_key':f"{room}:{task}:{s['segment_id']}:{s['revision']}:{kind}:{len(edits)}",
        }))
    if not segments:
        records.append(store.append_experience(wb.videos, {'room_id':room,'task_id':task,
            'source_rel_path':rel,'experience_type':'recording_no_content','conclusion':'no_content',
            'note':note,'dedupe_key':f'{room}:{task}:recording_no_content'}))
    store.generate_streamer_recommendation(wb.videos, room)
    return records
