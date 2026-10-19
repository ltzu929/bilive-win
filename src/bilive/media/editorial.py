"""Human decisions and edit snapshots, persisted atomically with the segment."""
from copy import deepcopy
from datetime import datetime, timezone


SNAPSHOT_FIELDS = (
    'revision', 'source_start', 'source_end', 'title', 'description', 'tags',
    'dynamic', 'tid', 'topic', 'season', 'decision', 'human_reviewed',
    'human_feedback', 'note', 'quality_score', 'completeness_score', 'confidence',
)


def snapshot(segment: dict) -> dict:
    return {key: deepcopy(segment[key]) for key in SNAPSHOT_FIELDS if key in segment}


def feedback(reason: str, uncertain: bool = False, kind: str = 'content') -> dict:
    if kind not in {'content', 'technical', 'duplicate'}:
        raise ValueError('invalid feedback kind')
    reason = reason.strip()
    if len(reason) > 1000:
        raise ValueError('取舍理由最多 1000 字')
    return {'reason': reason, 'uncertain': bool(uncertain), 'kind': kind}


def record_edit(segment: dict, action: str, before: dict, human: dict) -> None:
    after = snapshot(segment)
    history = segment.setdefault('editorial_history', [])
    # Repeated confirmation with no change is not a second independent judgment.
    if history and before == after and history[-1]['action'] == action and history[-1]['feedback'] == human:
        return
    history.append({
        'sequence': len(history) + 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'actor': 'human', 'action': action,
        'feedback': deepcopy(human), 'before': before, 'after': after,
    })
