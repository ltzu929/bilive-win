"""Protocol and queue tests use isolated fake network/data only."""
from types import SimpleNamespace
import json
import pytest

from bilive.vendor.upload.publication_options import PublicationOptionsClient, PublicationOptionsError
from bilive.vendor.upload.bilibili_web import BilibiliWebClient
from bilive.upload.season import join_published_season
from test_upload_consumer import setup

TOPIC = {'id': 12, 'name': '测试话题', 'mission_id': 34}
SEASON = {'id': 56, 'title': '测试合集', 'section_id': 78, 'section_title': '正片'}


class Response:
    def __init__(self, data=None, code=0):
        self.payload = {'code': code, 'data': data}
    def raise_for_status(self):
        pass
    def json(self):
        return self.payload


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs))
        return next(self.responses)
    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        return next(self.responses)


def season_response():
    return Response({'total': 1, 'seasons': [{'season': {'id':56, 'title':'测试合集', 'state':0, 'forbid':0},
        'sections': {'sections': [{'id':78, 'seasonId':56, 'title':'正片', 'state':0}]}}]})


def view_response(bvid='BVtest'):
    return Response({'archive': {'aid':90, 'bvid':bvid}, 'videos': [{'cid':91}]})


def test_current_lookup_structures_and_whitelist():
    session = Session(Response({'result': {'topics':[dict(TOPIC, description='描述', activity_sign='活动',
        act_protocol='规则', unexpected_private='must not surface')], 'page_info': {'has_more':True,'offset':20}, 'tips':'提示'}}),
        Response(dict(TOPIC, is_valid=True, mission_state=1, topic_description='描述', private='hidden')), season_response())
    client = PublicationOptionsClient(session, 'test-csrf')
    result = client.search_topics('测试', 10)
    assert result['has_more'] and result['offset'] == 20
    assert 'unexpected_private' not in result['items'][0]
    assert session.calls[0][2]['params'] == {'keywords':'测试','offset':10,'page_size':20}
    assert client.topic_info(TOPIC)['id'] == 12
    assert client.list_seasons()['items'] == [SEASON]


@pytest.mark.parametrize('changes', [{'is_valid':False}, {'mission_id':35}, {'mission_state':0}, {'name':'changed'}])
def test_stale_activity_is_rejected(changes):
    session = Session(Response({**TOPIC, 'is_valid':True, 'mission_state':1, **changes}))
    with pytest.raises(PublicationOptionsError):
        PublicationOptionsClient(session, 'csrf').topic_info(TOPIC)


def test_malformed_and_unavailable_responses_are_not_empty_success():
    for response in [Response({}, code=-101), Response({'result':{}}), Response(None)]:
        with pytest.raises(PublicationOptionsError):
            PublicationOptionsClient(Session(response), 'csrf').search_topics('test')


def test_plain_topic_needs_no_active_mission():
    topic = dict(TOPIC, mission_id=0)
    assert PublicationOptionsClient(Session(Response(dict(topic,is_valid=True,mission_state=0))), 'csrf').topic_info(topic)['mission_id'] == 0


def test_attach_uses_current_camelcase_protocol_and_confirms_remote_collection():
    session = Session(season_response(), view_response(), Response(code=-404), Response(None), Response({'id':56}))
    PublicationOptionsClient(session, 'csrf').add_to_season(90, 'BVtest', '标题', SEASON)
    posts = [call for call in session.calls if call[0] == 'POST']
    assert len(posts) == 1
    assert posts[0][1].endswith('/season/section/episodes/add')
    assert posts[0][2]['json'] == {'sectionId':78, 'episodes':[{'aid':90,'cid':91,'title':'标题'}]}
    assert posts[0][2]['params'] == {'csrf':'csrf'}


def test_lost_response_retry_does_not_add_twice():
    session = Session(season_response(), view_response(), Response({'id':56}))
    PublicationOptionsClient(session, 'csrf').add_to_season(90,'BVtest','title',SEASON)
    assert not any(call[0] == 'POST' for call in session.calls)


@pytest.mark.parametrize('view,current', [(view_response('BVother'),None), (view_response(),Response({'id':999}))])
def test_wrong_archive_or_other_collection_never_modified(view,current):
    session = Session(season_response(),view,current)
    with pytest.raises(PublicationOptionsError):
        PublicationOptionsClient(session,'csrf').add_to_season(90,'BVtest','title',SEASON)
    assert not any(call[0] == 'POST' for call in session.calls)


def test_submission_has_topic_activity_and_topic_tag():
    session = Session(Response({'bvid':'BVtest'}))
    BilibiliWebClient(session=session,csrf='csrf').submit_uploaded_video('remote',{'title':'title','tag':'切片','topic':TOPIC})
    body = session.calls[0][2]['json']
    assert (body['topic_id'],body['mission_id'],body['tag']) == (12,34,'切片,测试话题')
    assert body['topic_detail'] == {'from_topic_id':12,'from_source':'arc.web.search'}
    assert 'season_id' not in body


def test_topic_counts_toward_tag_limit_before_network():
    session = Session()
    with pytest.raises(ValueError,match='标签'):
        BilibiliWebClient(session=session,csrf='csrf').submit_uploaded_video('remote',{'title':'title',
            'tag':','.join(str(i) for i in range(10)), 'topic':TOPIC})
    assert session.calls == []


def test_published_is_saved_before_association_failure_and_retry_cannot_resubmit(setup):
    queue, video, client, counts, consumer = setup
    queue.stage(str(video), title='title', topic=TOPIC, season=SEASON)
    calls = []
    def add(*args):
        assert queue.get_by_path(str(video))['stage'] == 'published'
        calls.append(args)
        raise PublicationOptionsError('isolated failure')
    client.publication_options = SimpleNamespace(validate_options=lambda *args:{'topic':TOPIC,'season':SEASON}, add_to_season=add)
    metadata = []
    client.submit_uploaded_video = lambda remote, data: metadata.append(data.copy()) or {'code':0,'data':{'bvid':'BVtest','aid':90}}
    queue.activate(str(video))
    result = consumer.process_one()
    assert result['status'] == 'published' and result['season_status'] == 'failed'
    assert queue.get_by_path(str(video))['aid'] == 90
    assert metadata[0]['topic'] == TOPIC
    assert consumer.process_one()['status'] == 'idle'
    client.publication_options.add_to_season = lambda *args:calls.append(args)
    assert join_published_season(queue,client,str(video))['season_status'] == 'joined'
    assert join_published_season(queue,client,str(video))['season_status'] == 'joined'
    assert len(metadata) == 1 and len(calls) == 2 and counts['upload'] == 1


def test_preflight_failure_never_uploads_or_submits(setup):
    queue, video, client, counts, consumer = setup
    queue.stage(str(video),title='title',topic=TOPIC)
    def invalid(*args):
        raise PublicationOptionsError('话题已失效')
    client.publication_options = SimpleNamespace(validate_options=invalid)
    queue.activate(str(video))
    assert consumer.process_one()['status'] == 'queued'
    assert counts == {'upload':0,'submit':0}


def test_queue_clears_selection_and_rejects_protected_restage(setup):
    queue, video, *_ = setup
    queue.stage(str(video),title='title',topic=TOPIC,season=SEASON)
    assert json.loads(queue.get_by_path(str(video))['season']) == SEASON
    queue.stage(str(video),title='title')
    assert queue.get_by_path(str(video))['season'] is None
    queue.activate(str(video))
    with pytest.raises(ValueError,match='protected'):
        queue.stage(str(video),title='title',season=SEASON)
