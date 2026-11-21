from pathlib import Path
import runpy
import struct
from types import SimpleNamespace
import zlib

import pytest


PROBE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/probe_moss_cover.py'))


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 1536, 864, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\0' * (1536 * 3)) * 864))
            + chunk(b'IEND', b''))


def test_credentials_only_go_to_generation_endpoint(monkeypatch, tmp_path):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {
            'data': [{'url': 'https://assets.example.test/image.png'}],
            'usage': {'generated_images': 1},
        })
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(content=png(), raise_for_status=lambda: None)
    monkeypatch.setattr(PROBE['requests'], 'post', post)
    monkeypatch.setattr(PROBE['requests'], 'get', get)
    output = tmp_path / 'candidate'
    request = PROBE['build_request']('封面测试', PROBE['MODELS'][1], 42)
    result = PROBE['generate'](request, output, api_key='isolated-secret')
    assert calls[0][0] == 'https://api.mosi.cn/v1/images/generations'
    assert calls[0][1]['headers']['Authorization'] == 'Bearer isolated-secret'
    assert calls[1] == ('https://assets.example.test/image.png', {'timeout': 60})
    assert result['production_applied'] is False
    assert (output / 'cover.png').read_bytes() == png()
    assert all('isolated-secret' not in path.read_text(encoding='utf-8')
               for path in output.glob('*.json'))
    with pytest.raises(FileExistsError):
        PROBE['generate'](request, output, api_key='isolated-secret')
    assert len(calls) == 2


def test_missing_key_stops_before_network_and_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(PROBE['requests'], 'post', lambda *a, **kw: pytest.fail('unexpected request'))
    output = tmp_path / 'candidate'
    with pytest.raises(ValueError, match='MOSS_API_KEY'):
        PROBE['generate']({}, output, api_key='')
    assert not output.exists()


@pytest.mark.parametrize('payload', [{}, {'data': []}, {'data': [{'url': 'http://example.test/a.png'}]}])
def test_invalid_response_never_downloaded(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(PROBE['requests'], 'post', lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: payload))
    monkeypatch.setattr(PROBE['requests'], 'get', lambda *a, **kw: pytest.fail('unexpected download'))
    with pytest.raises(ValueError):
        PROBE['generate']({}, tmp_path / 'candidate', api_key='test')


def test_invalid_image_keeps_response_without_saving_cover(monkeypatch, tmp_path):
    monkeypatch.setattr(PROBE['requests'], 'post', lambda *a, **kw: SimpleNamespace(
        status_code=200, json=lambda: {'data': [{'url': 'https://assets.example.test/a.png'}]}))
    monkeypatch.setattr(PROBE['requests'], 'get', lambda *a, **kw: SimpleNamespace(
        content=b'<html>error</html>', raise_for_status=lambda: None))
    output = tmp_path / 'candidate'
    with pytest.raises(ValueError, match='PNG'):
        PROBE['generate']({}, output, api_key='test')
    assert (output / 'response.json').is_file()
    assert not (output / 'cover.png').exists()


def test_http_failure_is_not_retried(monkeypatch, tmp_path):
    calls = []
    def post(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(status_code=429)
    monkeypatch.setattr(PROBE['requests'], 'post', post)
    with pytest.raises(ValueError, match='HTTP 429'):
        PROBE['generate']({}, tmp_path / 'candidate', api_key='test')
    assert len(calls) == 1


@pytest.mark.parametrize('prompt, seed', [('', 0), ('a' * 4001, 0), ('测试', -1), ('测试', 2**53)])
def test_invalid_prompt_and_seed(prompt, seed):
    with pytest.raises(ValueError):
        PROBE['build_request'](prompt, PROBE['MODELS'][0], seed)
