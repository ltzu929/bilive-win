import json
from pathlib import Path

import pytest

from test_manual_workbench import env, create, subtitles, finalize


def test_manual_final_inherits_streamer_collection_and_explicit_clear_survives_reburn(env):
    from bilive.vendor.streamer_profiles import patch_streamer_profile, read_streamer_profile
    c, worker, wb, queue = env
    source = wb.videos / 'session.mp4'
    named = source.with_name('blive_123_session.mp4')
    source.rename(named)
    season = {'id':56,'section_id':78,'title':'主播合集','section_title':'正片'}
    patch_streamer_profile(wb.videos,'123',{'default_season':season})
    s = wb.points(named.name,.5,2.5,'title','reason')
    s = finalize(c,worker,subtitles(c,s))
    assert json.loads(queue.get_by_path(s['final_path'])['season']) == season
    s = wb.save_metadata(s['segment_id'],s['revision'],s['title'],s['description'],s['tags'],
                         publication_options={'season':None})
    assert queue.get_by_path(s['final_path'])['season'] is None
    s = finalize(c,worker,s)
    assert queue.get_by_path(s['final_path'])['season'] is None
    assert read_streamer_profile(wb.videos,'123')['default_season'] == season


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


def test_default_cover_and_preview_are_versioned_but_never_a_publish_gate(env):
    c, worker, wb, q = env
    s = finalize(c, worker, subtitles(c, create(c)))
    assert Path(s['cover_path']).read_bytes().startswith(b'\xff\xd8')
    assert c.get(f"/api/segments/{s['segment_id']}/cover").status_code == 200
    endpoint = f"/api/segments/{s['segment_id']}/preview"
    payload = {'expected_revision':s['revision'], 'ranges':[[0,.5], [1,2]]}
    assert c.post(endpoint, json=payload).json()['complete'] is False
    payload['ranges'] = [[.5,1]]
    assert c.post(endpoint, json=payload).json()['complete'] is True
    assert wb.detail(s['segment_id'])['preview_complete']
    payload['ranges'] = [[0,999]]
    assert c.post(endpoint, json=payload).status_code == 422
    # Publication metadata preserves the media's preview; subtitle edits invalidate it.
    s = c.post(f"/api/segments/{s['segment_id']}/metadata", json={
        'expected_revision':s['revision'], 'title':'new', 'tid':19}).json()
    assert s['preview_complete']
    assert c.post(endpoint, json={'expected_revision':s['revision']-1,'ranges':[[0,2]]}).status_code == 409
    s = subtitles(c,s)
    assert not wb.detail(s['segment_id'])['preview_complete']
    s = finalize(c,worker,s)
    assert not s['preview_complete']
    response = c.post('/api/actions',json={'action':'approve_publish','target':s['segment_id'],'revision':s['revision']})
    assert response.status_code == 200, response.text


def test_posting_draft_preserves_dynamic_through_save_reburn_and_approval(env):
    c, worker, wb, queue = env
    s = finalize(c, worker, subtitles(c, create(c)))
    url = f"/api/segments/{s['segment_id']}/metadata"
    payload = {'expected_revision':s['revision'], 'title':s['title'], 'dynamic':'这段对话太有意思了！'}
    s = c.post(url, json=payload).json()
    assert s['final_valid'] and not s['publish_approval']
    assert queue.get_by_path(s['final_path'])['stage'] == 'staged'
    assert queue.get_by_path(s['final_path'])['dynamic'] == payload['dynamic']
    # Older clients omitting the new optional field must preserve its value.
    s = c.post(url, json={'expected_revision':s['revision'], 'title':'更新标题'}).json()
    assert s['dynamic'] == payload['dynamic']
    s = finalize(c, worker, s)
    assert queue.get_by_path(s['final_path'])['dynamic'] == payload['dynamic']
    response = c.post('/api/actions', json={'action':'approve_publish','target':s['segment_id'],'revision':s['revision']})
    assert response.status_code == 200
    assert worker.run_once()
    assert queue.get_by_path(s['final_path'])['stage'] == 'queued'
    assert c.post(url, json={'expected_revision':s['revision'],'title':'禁止改写','dynamic':''}).status_code == 409
    assert wb.load(s['segment_id'])['dynamic'] == payload['dynamic']


def test_dynamic_validation_and_explicit_clear_preserve_saved_media(env):
    c, worker, wb, queue = env
    s = finalize(c, worker, subtitles(c, create(c)))
    url = f"/api/segments/{s['segment_id']}/metadata"
    invalid = c.post(url, json={'expected_revision':s['revision'],'title':s['title'],'dynamic':'字'*234})
    assert invalid.status_code == 422
    assert wb.load(s['segment_id'])['revision'] == s['revision']
    for dynamic in ['已填写的动态', '']:
        response = c.post(url, json={'expected_revision':s['revision'],'title':s['title'],'dynamic':dynamic})
        assert response.status_code == 200, response.text
        s = response.json()
        assert s['final_valid'] and s['dynamic'] == dynamic
        assert queue.get_by_path(s['final_path'])['dynamic'] == dynamic
