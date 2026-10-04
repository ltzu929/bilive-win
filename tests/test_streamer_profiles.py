from pathlib import Path
import json
import pytest
from fastapi.testclient import TestClient
from bilive.vendor import streamer_profiles as profiles
from bilive.lifecycle.profiles import profile_for_source, record_review
from bilive.domain.models import SubtitleLine
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench
from bilive.jobs.action_jobs import ActionJobStore
from bilive.upload.queue import UploadQueue
from bilive.db.conn import init_upload_db


def test_profile_reads_do_not_write_and_rooms_are_isolated(tmp_path):
    root = tmp_path/'missing'
    assert profiles.read_streamer_profile(root,'123')['display_name'] == ''
    assert profiles.streamer_evidence_summary(root,'123')['evidence_status'] == 'insufficient_evidence'
    assert not root.exists()
    profiles.patch_streamer_profile(root,'123',{'display_name':'主播甲','default_tags':['甲'],
        'default_slice_options':{'burst_ratio':4},'default_subtitle_style':{'font_size':28}})
    assert profile_for_source(root,'123 - 甲/session.mp4')['display_name'] == '主播甲'
    assert profile_for_source(root,'456 - 乙/session.mp4')['display_name'] == ''
    with pytest.raises(ValueError):profiles.patch_streamer_profile(root,'123',{'quality_threshold':0.1})
    with pytest.raises(ValueError):profiles.read_streamer_profile(root,'../outside')
    path=profiles.streamer_state_path(root,'123');path.write_text('{broken')
    with pytest.raises(ValueError):profiles.patch_streamer_profile(root,'123',{'display_name':'overwrite'})
    assert path.read_text() == '{broken'


def sample(root, room, number, kind):
    return profiles.append_experience(root,{'room_id':room,'experience_type':kind,
        'conclusion':'positive' if kind == 'positive' else 'negative' if kind == 'negative' else 'technical_failure',
        'dedupe_key':f'{room}:{number}', 'reason_type':'边界完整性'})


def test_evidence_gate_dedupe_and_explicit_application(tmp_path):
    for i in range(6):sample(tmp_path,'123',i,'technical_failure')
    for i in range(4):sample(tmp_path,'123',10+i,'positive')
    sample(tmp_path,'456',1,'negative')
    assert profiles.generate_streamer_recommendation(tmp_path,'123')['evidence_status'] == 'insufficient_evidence'
    sample(tmp_path,'123',14,'negative');sample(tmp_path,'123',14,'negative')
    first=profiles.generate_streamer_recommendation(tmp_path,'123')['recommendations']
    assert len(first) == 1 and first[0]['sample_size'] == 5
    assert profiles.read_streamer_profile(tmp_path,'123')['approved_guidance'] == ''
    assert len(profiles.generate_streamer_recommendation(tmp_path,'123')['recommendations']) == 1
    identifier=first[0]['recommendation_id']
    out=profiles.apply_streamer_recommendation(tmp_path,'123',identifier)
    assert out['profile']['approved_guidance'] and out['recommendation']['status'] == 'applied'
    with pytest.raises(FileNotFoundError):profiles.apply_streamer_recommendation(tmp_path,'456',identifier)
    assert profiles.read_streamer_profile(tmp_path,'456')['approved_guidance'] == ''


def test_review_technical_drop_keeps_evidence_and_is_not_negative(tmp_path):
    videos=tmp_path/'Videos';source=videos/'123 - 甲/session.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'fixture')
    source.with_suffix('.xml').write_text('<i><d p="1,1,25">弹幕依据</d></i>',encoding='utf-8')
    artifacts=tmp_path/'artifacts';subs=SubtitleStore(artifacts/'subtitles');db=tmp_path/'upload.db';init_upload_db(db)
    wb=Workbench(videos,artifacts,ActionJobStore(tmp_path/'jobs'),subs,UploadQueue(db))
    s=wb.points('123 - 甲/session.mp4',0,2,'标题','人工补漏')
    subs.save(s['segment_id'],2,[SubtitleLine(1,0,1,'字幕依据')],1)
    s.update(decision='judge_failed',processing_error='ASR failure');wb.save(s)
    s=wb.drop(s['segment_id'],1,'技术故障后放弃')
    records=record_review(wb,'123 - 甲/session.mp4','task',[s],'终审')
    assert records[0]['experience_type'] == 'technical_failure'
    assert records[0]['transcript_summary'] == '字幕依据'
    assert records[0]['danmaku_summary']['text'] == '弹幕依据'
    assert profiles.streamer_evidence_summary(videos,'123')['sample_size'] == 0
    source.unlink()
    assert profiles.read_experiences(videos,room_id='123')[0]['source_rel_path']


def test_subtitle_style_is_written_and_rejects_injection(tmp_path):
    subs=SubtitleStore(tmp_path/'subtitles')
    subs.save('segment',2,[SubtitleLine(1,0,1,'字幕')],1)
    output=subs.write_ass('segment',tmp_path/'style.ass',{'font_name':'Noto Sans SC','font_size':32,'margin_v':80,'alignment':8,'outline':2})
    assert 'Style: Default,Noto Sans SC,32,' in output.read_text(encoding='utf-8')
    assert ',1,2.0,0,8,10,10,80,1' in output.read_text(encoding='utf-8')
    for appearance in ({'font_name':'evil\n[Events]'}, {'outline':float('nan')}, {'alignment':99}, {'primary_colour':'red'}, {'font_size':-1}):
        with pytest.raises(ValueError):profiles.patch_streamer_profile(tmp_path,'123',{'default_subtitle_style':appearance})


def test_profile_api_persistence_and_cross_origin_protection(tmp_path,monkeypatch):
    import importlib
    api = importlib.import_module('bilive.api.app')
    monkeypatch.setattr(api.workbench,'videos',tmp_path/'Videos')
    with TestClient(api.app) as client:
        url='/api/streamers/123/profile'
        assert client.get(url).status_code == 200
        assert not (tmp_path/'Videos').exists()
        assert client.post(url,json={'display_name':'主播甲'}).status_code == 200
        assert client.get(url).json()['profile']['display_name'] == '主播甲'
        assert client.post(url,json={'display_name':'覆盖'},headers={'Origin':'https://foreign.invalid'}).status_code == 403
        assert client.get('/api/streamers/123/experiences?limit=101').status_code == 422
