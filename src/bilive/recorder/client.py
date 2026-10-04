"""Narrow native room API adapter; credentials stay in the local backend."""
from __future__ import annotations
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class RecorderUnavailable(Exception):
    pass


class RecorderClient:
    def __init__(self, port=2233, opener=urlopen):
        self.port, self.opener = port, opener

    def request(self, path, method='GET', payload=None):
        key = os.environ.get('BLREC_API_KEY') or os.environ.get('RECORD_KEY')
        if not key:
            raise RecorderUnavailable('录制器 API key 未配置')
        headers = {'X-API-Key':key, 'Content-Type':'application/json'}
        body = json.dumps(payload).encode() if payload is not None else (b'' if method == 'POST' else None)
        request = Request(f'http://127.0.0.1:{self.port}/api/v1/tasks{path}',
                          data=body, headers=headers, method=method)
        try:
            with self.opener(request, timeout=10) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            # Preserve native conflict/not-found status without reflecting arbitrary upstream secrets.
            if exc.code in {401, 403}:
                raise RecorderUnavailable('录制器鉴权失败') from None
            if exc.code in {404, 409, 422}:
                raise ValueError(f'录制器拒绝操作（HTTP {exc.code}）') from None
            raise RecorderUnavailable(f'录制器不可用（HTTP {exc.code}）') from None
        except (URLError, OSError, ValueError):
            raise RecorderUnavailable('录制器连接或响应不可用') from None

    def rooms(self, page=1, size=20):
        data = self.request(f'/data?page={page}&size={size}')
        if not isinstance(data, list):
            raise RecorderUnavailable('录制器房间列表响应无效')
        # Room management needs no signed stream URL, Cookie or task parameters.
        fields = {'room_info':('room_id','title','live_status','cover'), 'user_info':('name','uname','uid'),
                  'task_status':('monitor_enabled','recorder_enabled','running_status',
                                 'rec_elapsed','rec_total','rec_rate','danmu_total','danmu_rate')}
        return [{section:{key:record.get(section, {}).get(key) for key in keys}
                 for section,keys in fields.items()} for record in data]

    def control(self, room_id: int, action: str):
        if room_id <= 0:
            raise ValueError('room_id must be positive')
        actions = {'add':('', 'POST', None), 'remove':('', 'DELETE', None),
                   'start':('/start', 'POST', None), 'stop':('/stop', 'POST', {'force':False,'background':True}),
                   'monitor_start':('/monitor/enable','POST',None),
                   'monitor_stop':('/monitor/disable','POST',None),
                   'enable':('/recorder/enable','POST',None),
                   'disable':('/recorder/disable','POST',{'force':False,'background':True}),
                   'cut':('/cut','POST',None)}
        if action not in actions:
            raise ValueError('unknown room action')
        suffix, method, payload = actions[action]
        return self.request(f'/{room_id}{suffix}', method, payload)
