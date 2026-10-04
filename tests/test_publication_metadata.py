import json
from pathlib import Path

import pytest

from test_manual_workbench import env, create, subtitles, finalize


def test_metadata_updates_staged_queue_and_keeps_final_and_subtitles(env):
    c, worker, wb, q = env
    s = finalize(c, worker, subtitles(c, create(c)))
    media = Path(s['final_path']).read_bytes()
    payload = dict(expected_revision=s['revision'], title='新的标题', description='多行\n简介', tags=['TAG', 'TAG', ' 测试 '])
    response = c.post(f"/api/segments/{s['segment_id']}/metadata", json=payload)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated['revision'] == s['revision']+1
    assert updated['title'] == '新的标题' and updated['tags'] == ['TAG', '测试']
    assert updated['subtitles'] == s['subtitles'] and updated['final_valid']
    assert updated['final_path'] == s['final_path'] and Path(updated['final_path']).read_bytes() == media
    row = q.get_by_path(s['final_path'])
    assert row['stage'] == 'staged' and row['title'] == '新的标题'
    assert row['description'] == '多行\n简介' and json.loads(row['tags']) == ['TAG', '测试']
    assert c.post(f"/api/segments/{s['segment_id']}/metadata", json=payload).status_code == 409
    q.activate(s['final_path'])
    payload['expected_revision'] = updated['revision']
    payload['title'] = '不能覆盖已激活队列'
    assert c.post(f"/api/segments/{s['segment_id']}/metadata", json=payload).status_code == 409
    assert wb.load(s['segment_id'])['title'] == '新的标题'
    assert q.get_by_path(s['final_path'])['title'] == '新的标题'


@pytest.mark.parametrize('patch', [{'title': ''}, {'title': '字'*81}, {'description': '字'*2001},
                                  {'tags': ['字'*21]}, {'tags': [str(i) for i in range(11)]}])
def test_invalid_metadata_preserves_segment(env, patch):
    c, _, wb, _ = env
    s = create(c)
    payload = dict(expected_revision=s['revision'], title='标题', description='', tags=[])
    payload.update(patch)
    assert c.post(f"/api/segments/{s['segment_id']}/metadata", json=payload).status_code == 422
    assert wb.load(s['segment_id']) == s
