"""Adapt pinned human evidence rules to current workbench records."""
from __future__ import annotations
import re
import hashlib
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


def has_human_review(segment: dict) -> bool:
    return bool(segment.get('editorial_history') or segment.get('human_reviewed')
                or segment.get('selection_origin') == 'manual' or segment.get('decision') == 'manual_keep'
                or segment.get('human_feedback') or segment.get('drop_reason') or segment.get('missed_reason'))


def record_segment_review(wb, s: dict, *, task: str | None = None, classification: dict | None = None):
    """Collect one human revision without requiring source media or finishing its lifecycle."""
    from bilive.domain.models import task_id_from_source_relpath
    from bilive.media.editorial import feedback
    rel = s['source_relpath']
    room = room_from_source(rel)
    if not room:
        return None
    source = (wb.videos / rel).resolve()
    if not source.is_relative_to(wb.videos.resolve()):
        raise ValueError('source path escapes videos root')
    human = s.get('human_feedback', {})
    human_reason = human.get('reason', '') if 'human_feedback' in s else s.get('drop_reason') or s.get('missed_reason') or ''
    edits = s.get('editorial_history', [])
    if classification:
        human = feedback(human_reason, human.get('uncertain', False), classification['kind'])
    technical = bool(human.get('kind') == 'technical' or s.get('processing_error') or s.get('technical_failure') or s['decision'] == 'judge_failed')
    duplicate = s['decision'] == 'drop' and (human.get('kind') == 'duplicate' or store.is_duplicate_experience(s))
    correction = {'boundary': 'boundary_correction', 'understanding': 'understanding_correction',
                  'publication': 'publication_preference', 'mixed': 'mixed_feedback'}.get(human.get('kind'))
    kind = ('uncertain' if human.get('uncertain') else
            'duplicate' if duplicate else
            'technical_failure' if technical else
            correction if correction else
            'negative' if s['decision'] == 'drop' else
            'editorial_correction' if s.get('human_reviewed') is False else
            'missed_segment_positive' if s.get('missed_reason') or s.get('selection_origin') == 'manual' else
            'positive' if s['decision'] == 'manual_keep' or s.get('human_reviewed') or not edits else
            'editorial_correction')
    initial = edits[0].get('before', {}) if edits else s
    # A newly created human selection has no model baseline.
    transcript = ' '.join(v['text'] for v in wb.subtitles.load(s['segment_id'])['lines'])[:1000]
    texts = []
    xml = source.with_suffix('.xml')
    if xml.is_file():
        from bilive.analysis.pipeline import read_danmaku
        texts = [text for ts, text in read_danmaku(xml) if s['source_start'] <= ts <= s['source_end']]
    record = store.append_experience(wb.videos, {
            'room_id':room, 'task_id':task or task_id_from_source_relpath(rel), 'source_rel_path':rel, 'segment_id':s['segment_id'],
            'title':s.get('title', ''),
            'experience_type':kind, 'conclusion':'positive' if kind in {'positive', 'missed_segment_positive'} else kind,
            'reason_type':'technical_failure' if technical else str(human_reason or 'content_review')[:100],
            'note':human_reason, 'original_note':s.get('note', ''),
            'human_reason':human_reason, 'human_feedback':human,
            'editorial_reason':edits[-1].get('feedback', {}).get('reason', '') if edits else '',
            'original_human_feedback':s.get('human_feedback') if classification else None,
            'human_decision':s['decision'], 'selection_origin':s.get('selection_origin', 'unknown'),
            'editorial_history':edits,
            'revision':s['revision'], 'editorial_sequence':len(edits),
            'classification':classification,
            'review_note':(classification or {}).get('review_note', ''),
            'duplicate_of':s.get('duplicate_of'),
            'model_judgment':initial.get('decision') if initial.get('decision') != 'manual_keep' else None,
            'model_scores':{k:initial.get(k) for k in ('quality_score','completeness_score','confidence')},
            'start_seconds':s['source_start'], 'end_seconds':s['source_end'],
            'transcript_summary':transcript, 'danmaku_summary':{'count':len(texts),'text':' '.join(texts)[:1000]},
            'dedupe_key':f"review:v2:{room}:{s['segment_id']}:{s['revision']}:{len(edits)}" + (
                ':' + hashlib.sha256(str(classification['review_note']).encode()).hexdigest()[:16]
                if (classification or {}).get('review_note') else ''),
        })
    return record


def sync_review_feedback(wb, *, room: str | None = None, classifications: dict | None = None, write: bool = False):
    """Preview/backfill explicit human evidence, including records whose media was cleaned."""
    selected = []
    from bilive.media.editorial import feedback
    if room is not None:
        store._validate_room_id(room)
    for item in (classifications or {}).values():
        feedback('', kind=item['kind'])
        feedback(item.get('review_note', ''))
    for p in sorted(wb.root.glob('*.json')):
        s = wb.load(p.stem)
        if has_human_review(s) and (room is None or room_from_source(s['source_relpath']) == room):
            selected.append(s)
    records = []
    if write:
        for s in selected:
            records.append(record_segment_review(wb, s, classification=(classifications or {}).get(s['segment_id'])))
    return {'segments': len(selected), 'segment_ids': [s['segment_id'] for s in selected], 'records': records}


def record_review(wb, rel: str, task: str, segments: list[dict], note: str):
    room = room_from_source(rel)
    if not room:
        return []
    records = [record_segment_review(wb, s, task=task) for s in segments]
    if not segments:
        records.append(store.append_experience(wb.videos, {'room_id':room,'task_id':task,
            'source_rel_path':rel,'experience_type':'recording_no_content','conclusion':'no_content',
            'note':note,'dedupe_key':f'{room}:{task}:recording_no_content'}))
    store.generate_streamer_recommendation(wb.videos, room)
    return records
