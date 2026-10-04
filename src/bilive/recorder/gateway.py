"""Keep native /api/v1 separate from /studio-api and /workbench on 2233."""
from pathlib import Path
from bilive.vendor.studio_proxy import StudioApiMiddleware, _header_map, _send_error


class RecorderGateway:
    def __init__(self, app, upstream=None):
        self.native = app
        self.studio = StudioApiMiddleware(app, upstream=upstream)
        self.workbench = StudioApiMiddleware(app, upstream=upstream, prefix='/workbench')

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            return await self.native(scope, receive, send)
        headers = _header_map(scope)
        origin = headers.get('origin')
        own = f"{scope.get('scheme', 'http')}://{headers.get('host', '')}"
        if scope.get('method') not in {'GET', 'HEAD', 'OPTIONS'} and origin and origin != own:
            return await _send_error(send, 403, 'cross-origin write rejected')
        path = scope.get('path', '')
        if path == '/ngsw-worker.js' and scope.get('method') in {'GET','HEAD'}:
            body=Path(__file__).with_name('ngsw-retire.js').read_bytes()
            await send({'type':'http.response.start','status':200,'headers':[
                (b'content-type',b'application/javascript'),(b'cache-control',b'no-store'),
                (b'content-length',str(len(body)).encode())]})
            return await send({'type':'http.response.body','body':b'' if scope['method']=='HEAD' else body})
        if path == '/ngsw.json':
            return await _send_error(send,404,'legacy offline cache retired')
        if path == '/workbench' or (path == '/' and scope.get('method') in {'GET','HEAD'}):
            await send({'type':'http.response.start', 'status':307,
                        'headers':[(b'location', b'/workbench/'), (b'content-length', b'0')]})
            return await send({'type':'http.response.body', 'body':b''})
        if path.startswith('/workbench/'):
            return await self.workbench(scope, receive, send)
        return await self.studio(scope, receive, send)
