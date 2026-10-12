"""Isolated browser acceptance; creator API is faked and uploader never starts."""
from pathlib import Path
from types import SimpleNamespace
import importlib
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    import uvicorn
    from bilive.config.settings import Settings
    from bilive.db.conn import init_upload_db
    from bilive.jobs.action_jobs import ActionJobStore
    from bilive.media.workbench import Workbench
    from bilive.media.subtitle_store import SubtitleStore
    from bilive.upload.queue import UploadQueue

    output = ROOT / 'output/playwright/publication-options'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='publication-ui-', dir=ROOT / '.runtime') as temp:
        folder = Path(temp)
        videos = folder / 'Videos'
        rel = '123 - 验证主播/blive_123_2026-10-11-120000.mp4'
        source = videos / rel
        source.parent.mkdir(parents=True)
        subprocess.run(['ffmpeg','-y','-f','lavfi','-i','color=c=black:s=160x90:d=32',
                        '-c:v','libx264','-preset','ultrafast',str(source)], check=True,capture_output=True)
        source.with_suffix('.xml').write_text('<i/>')
        db = folder / 'uploads.db'
        init_upload_db(db)
        queue = UploadQueue(db)
        artifacts = folder / 'artifacts'
        jobs = ActionJobStore(folder / 'jobs')
        subs = SubtitleStore(artifacts / 'subtitles')
        wb = Workbench(videos,artifacts,jobs,subs,queue)
        settings = Settings()
        settings.paths.videos_dir, settings.paths.db_path = videos, db
        wb.settings_provider = lambda:settings
        segment = wb.points(rel,0,2,'接口验证片段','isolated UI acceptance')
        final = artifacts / segment['segment_id'] / 'final_output.mp4'
        final.parent.mkdir(parents=True)
        final.write_bytes(b'isolated-final')
        queue.stage(str(final),title=segment['title'])
        segment['final_path'] = str(final)
        wb.save(segment)
        topic = {'id':12,'name':'测试话题','mission_id':34}
        season = {'id':56,'title':'测试合集','section_id':78,'section_title':'正片'}
        joins = []
        options = SimpleNamespace(search_topics=lambda *args:{'items':[topic],'has_more':False},
            topic_info=lambda value:{**value,'description':'测试话题说明','activity_description':'测试活动', 'tips':'请核对规则'},
            list_seasons=lambda:{'items':[season]}, validate_options=lambda **values:values,
            add_to_season=lambda *args:joins.append(args))
        api = importlib.import_module('bilive.api.app')
        api.settings, api.workbench, api.jobs, api.subtitles, api.upload_queue = settings, wb, jobs, subs, queue
        api.worker_control = SimpleNamespace(runtime=folder / 'runtime', wake=lambda:{'state':'isolated'}, status=lambda:{'state':'offline'})
        api.publication_client = lambda:SimpleNamespace(publication_options=options)
        api.recorder_client = SimpleNamespace(rooms=lambda *args:[])
        @api.app.get('/qa/publication-state')
        def state():
            return {'segment':wb.load(segment['segment_id']), 'queue':queue.get_by_path(str(final)), 'joins':joins}
        @api.app.post('/qa/publication-lock')
        def lock():
            saved = wb.load(segment['segment_id'])
            saved['publish_approval'] = True
            wb.save(saved)
            return {'ok':True}
        @api.app.post('/qa/publication-published')
        def published():
            queue.stage(str(final),title='title',season=season)
            queue.mark_published(str(final),'BVtest',90)
            queue.save_season_result(str(final),'isolated association failure')
            return {'ok':True}
        sock = socket.socket()
        sock.bind(('127.0.0.1',0))
        sock.listen()
        url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(api.app,log_level='error'))
        thread = threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
        thread.start()
        errors = []
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            subprocess.run(['node', str(ROOT / 'scripts/verify_publication_options_ui.cjs'), url, str(output)],check=True)
            (output / 'result.json').write_text(json.dumps({'passed':True,'console_errors':errors,
                'checks':['streamer default save/reload/inheritance/clear','search/select','draft reload','save to queue','clear selection','approval locks',
                          'collection-only retry'], 'creator_network':'mocked','real_publication':False},indent=2))
            print('PUBLICATION_OPTIONS_UI_PASS',str(output))
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            sock.close()


if __name__ == '__main__':
    main()
