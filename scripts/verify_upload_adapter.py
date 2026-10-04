"""Isolated installed-bilitool protocol check; every network boundary is mocked."""
from __future__ import annotations
import json
import os
import tempfile
from contextlib import ExitStack
from importlib.metadata import distribution
from pathlib import Path
from unittest.mock import patch
import requests

from bilive.config.settings import UploadConfig
from bilive.db.conn import init_upload_db
from bilive.upload.queue import UploadQueue
from bilive.upload.consumer import UploadConsumer
from bilive.vendor.upload.bilibili_runtime import BilibiliRuntimeClient

PIN = 'aefaff618235d7f94db35213a1c938126b25cac7'


def main():
    dist = distribution('bilitool')
    origin = json.loads(dist.read_text('direct_url.json') or '{}')
    assert origin.get('vcs_info', {}).get('commit_id') == PIN, 'installed bilitool source does not match pin'
    from bilitool.upload.bili_upload import BiliUploader
    from bilitool.model import model
    from bilitool.controller import upload_controller
    calls = {'uploads':0,'posts':0}
    class Response:
        def __init__(self,data):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    class Session(requests.Session):
        def get(self,url,**kwargs):
            assert url == 'https://api.bilibili.com/x/web-interface/nav'
            return Response({'data':{'isLogin':True,'uname':'isolated','mid':123}})
        def post(self,url,**kwargs):
            assert url == 'https://member.bilibili.com/x/vu/web/add/v3'
            assert kwargs['json']['videos'][0]['filename'] == 'qa-remote'
            assert kwargs['json']['title'] == '隔离协议验收'
            calls['posts'] += 1
            return Response({'code':0,'data':{'bvid':'BVisolated'}})
    with tempfile.TemporaryDirectory(prefix='bilive-upload-protocol-') as directory:
        root=Path(directory);video=root/'final.mp4';video.write_bytes(b'isolated upload fixture')
        config=root/'bilitool-config.json'
        previous=os.environ.get('BILITOOL_CONFIG_PATH')
        os.environ['BILITOOL_CONFIG_PATH']=str(config)
        try:
            db=root/'queue.db';init_upload_db(db);queue=UploadQueue(db)
            queue.stage(str(video),title='隔离协议验收',description='isolated',tags=['qa'])
            queue.activate(str(video))
            client=BilibiliRuntimeClient(cookies={'SESSDATA':'isolated-cookie','bili_jct':'isolated-csrf'},
                                        session=Session())
            original_model=model.Model
            def chunks(uploader,**kwargs):
                assert kwargs['fileio'].read() == video.read_bytes()
                assert uploader.session is client.session
                assert 'isolated-cookie' in uploader.headers['Cookie']
                calls['uploads'] += 1
            def forbidden_publish(*args,**kwargs):
                raise AssertionError('legacy bilitool publishing must never be called')
            with ExitStack() as stack:
                stack.enter_context(patch.object(BiliUploader,'probe',lambda self:('https://mock.invalid/','qa','v1')))
                stack.enter_context(patch.object(BiliUploader,'preupload',lambda self,**kw:
                    {'upos_uri':'upos://qa-remote.mp4','auth':'fake','biz_id':1,'chunk_size':1024}))
                stack.enter_context(patch.object(BiliUploader,'get_upload_video_id',lambda self,**kw:
                    {'upload_id':'isolated','key':'/qa-remote.mp4'}))
                stack.enter_context(patch.object(BiliUploader,'upload_video_in_chunks',chunks))
                stack.enter_context(patch.object(BiliUploader,'finish_upload',lambda self,**kw:None))
                stack.enter_context(patch.object(BiliUploader,'publish_video',forbidden_publish))
                consumer=UploadConsumer(queue,UploadConfig(delete_after_success=False),client,root/'upload.lock')
                assert consumer.process_one()['status'] == 'published'
                assert consumer.process_one()['status'] == 'idle'
            assert calls == {'uploads':1,'posts':1}
            assert model.Model is original_model and upload_controller.Model is original_model
            assert 'isolated-cookie' not in config.read_text(encoding='utf-8')
            assert 'isolated-csrf' not in config.read_text(encoding='utf-8')
            assert queue.get_by_path(str(video))['bvid'] == 'BVisolated'
            print('UPLOAD_ADAPTER_PASS installed pinned bilitool controller + runtime Cookie injection + Web submit + durable published queue; network mocked')
        finally:
            if previous is None:os.environ.pop('BILITOOL_CONFIG_PATH',None)
            else:os.environ['BILITOOL_CONFIG_PATH']=previous


if __name__ == '__main__':main()
