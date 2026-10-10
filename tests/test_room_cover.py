from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bilive.recorder.cover import CoverUnavailable, read_cover


@pytest.fixture(autouse=True)
def isolated_cover_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('BILIVE_RUNTIME_DIR', str(tmp_path))
    read_cover.cache_clear()
    yield
    read_cover.cache_clear()


@pytest.mark.parametrize('url', [
    'http://i0.hdslb.com/bfs/live/cover.jpg',
    'https://127.0.0.1/bfs/live/cover.jpg',
    'https://i0.hdslb.com.attacker.test/bfs/live/cover.jpg',
    'https://user:secret@i0.hdslb.com/bfs/live/cover.jpg',
    'https://i0.hdslb.com:8443/bfs/live/cover.jpg',
    'https://i0.hdslb.com/other/cover.jpg',
])
def test_cover_rejects_non_public_image_sources(url):
    with pytest.raises(CoverUnavailable):
        read_cover(url)


def test_cover_direct_read_and_cache(monkeypatch):
    read_cover.cache_clear()
    calls = []
    class Response:
        status_code = 200
        headers = {'Content-Type': 'image/jpeg'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield b'public image'
    class Session:
        trust_env = True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            assert not self.trust_env
            assert kwargs['allow_redirects'] is False
            calls.append(url)
            return Response()
    monkeypatch.setattr('bilive.recorder.cover.requests.Session', Session)
    url = 'https://i0.hdslb.com/bfs/live/isolated.jpg'
    assert read_cover(url) == (b'public image', 'image/jpeg')
    assert read_cover(url) == (b'public image', 'image/jpeg')
    assert calls == [url + '@840w_472h_1c.webp']
    read_cover.cache_clear()
    # A fresh process's empty memory cache can still reuse the disk copy.
    assert read_cover(url) == (b'public image', 'image/jpeg')
    assert len(calls) == 1
    # A changed cover URL must fetch the new image.
    assert read_cover(url.replace('isolated', 'changed')) == (b'public image', 'image/jpeg')
    assert len(calls) == 2


def test_disk_cache_is_bounded_and_disk_failure_does_not_break_image(monkeypatch, tmp_path):
    from bilive.recorder import cover
    for index in range(35):
        cover._save_disk_cover(str(index), b'image', 'image/webp')
    assert len(list(cover.cover_cache_dir().glob('*.webp'))) == 32
    assert not list(cover.cover_cache_dir().glob('*.tmp'))
    blocked = tmp_path / 'file-not-directory'
    blocked.write_bytes(b'file')
    monkeypatch.setenv('BILIVE_RUNTIME_DIR', str(blocked))
    cover._save_disk_cover('blocked', b'image', 'image/webp')
    assert cover._read_disk_cover('blocked') is None


@pytest.mark.parametrize('status,mime,body', [
    (302, 'image/jpeg', b'image'),
    (200, 'text/html', b'error page'),
    (200, 'image/jpeg', b''),
    (200, 'image/jpeg', b'x' * (2 * 1024 * 1024 + 1)),
], ids=['redirect', 'non-image', 'empty', 'oversized'])
def test_cover_does_not_serve_redirects_non_images_or_unbounded_content(monkeypatch, status, mime, body):
    read_cover.cache_clear()
    class Response:
        status_code = status
        headers = {'Content-Type': mime}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield body
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr('bilive.recorder.cover.requests.Session', Session)
    with pytest.raises(CoverUnavailable):
        read_cover('https://i0.hdslb.com/bfs/live/rejected.jpg')
    read_cover.cache_clear()


def test_cover_endpoint_uses_room_data_and_preserves_absent_state(monkeypatch):
    import importlib
    api = importlib.import_module('bilive.api.app')
    calls = []
    def request(path):
        calls.append(path)
        return {'room_info': {'cover': 'https://i0.hdslb.com/bfs/live/endpoint.jpg'}}
    monkeypatch.setattr(api, 'recorder_client', SimpleNamespace(request=request))
    monkeypatch.setattr('bilive.recorder.cover.read_cover', lambda url: (b'image', 'image/jpeg'))
    client = TestClient(api.app)
    result = client.get('/api/rooms/123/cover')
    assert calls == ['/123/data']
    assert result.status_code == 200 and result.content == b'image'
    assert result.headers['content-type'] == 'image/jpeg'
    assert result.headers['x-content-type-options'] == 'nosniff'
    monkeypatch.setattr(api, 'recorder_client', SimpleNamespace(request=lambda path: {'room_info': {}}))
    assert client.get('/api/rooms/123/cover').status_code == 404
    assert client.get('/api/rooms/0/cover').status_code == 422
