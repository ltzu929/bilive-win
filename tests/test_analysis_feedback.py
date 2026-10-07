import json

from bilive.media.evidence import recording_evidence
from test_source_review import env


def test_evidence_summary_counts_all_pages_and_distinguishes_judgment_failure(env):
    wb, _, task = env
    history = wb.videos / 'session.mp4.task.json'
    history.write_text(json.dumps({'status': 'done', 'candidates': [
        {'state': 'drop'}, {'state': 'failed'}, {'state': 'review'},
        {'state': 'reviewed', 'judgments': [{'judge_status': 'judge_failed'}]},
        {'state': 'processing'}, {'state': 'reviewed'},
    ]}), encoding='utf-8')
    before = history.read_bytes()
    evidence = recording_evidence(wb, task, limit=1, offset=4)
    assert evidence['candidates'] == [{'state': 'processing'}]
    assert evidence['total'] == 6
    assert evidence['summary'] == {'completed': 5, 'dropped': 1, 'failed': 2, 'needs_review': 1}
    assert history.read_bytes() == before


def test_unanalysed_recording_has_no_false_completion(env):
    wb, _, task = env
    evidence = recording_evidence(wb, task)
    assert evidence['history_available'] is False
    assert evidence['status'] is None and evidence['total'] == 0
    assert evidence['summary']['completed'] == 0
