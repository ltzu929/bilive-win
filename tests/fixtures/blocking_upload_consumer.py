"""Isolated real-process consumer: mock network, stop during an in-flight upload."""
import sys,time
from pathlib import Path
from bilive.config.settings import UploadConfig
from bilive.upload.queue import UploadQueue
from bilive.upload.consumer import UploadConsumer

db,runtime=map(Path,sys.argv[1:])
class Client:
    def check_login(self):return {'isLogin':True}
    def upload_file(self,path,metadata):
        (runtime/'network-started').touch()
        deadline=time.monotonic()+20
        while not (runtime/'network-release').exists():
            if time.monotonic()>deadline:raise TimeoutError('isolated network barrier')
            time.sleep(.02)
        return 'isolated-confirmed-remote'
    def submit_uploaded_video(self,remote,metadata):
        (runtime/'submission-completed').touch()
        return {'code':0,'data':{'bvid':'BV-isolated-stop'}}

UploadConsumer(UploadQueue(db),UploadConfig(poll_interval=.01,delete_after_success=False),Client(),
               runtime/'upload.lock').run()
