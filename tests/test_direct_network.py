import os
from urllib.request import proxy_bypass

import pytest
import requests

from bilive.network import use_direct_network
from bilive.vendor.upload.bilibili_runtime import BilibiliRuntimeClient


@pytest.fixture
def poisoned_proxy(monkeypatch):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.setenv(name, 'http://127.0.0.1:9')
    monkeypatch.setenv('NO_PROXY', '')
    monkeypatch.setenv('no_proxy', '')


def test_bilive_bypasses_proxy_for_unknown_cdn_and_api_hosts(poisoned_proxy):
    use_direct_network()
    for host in ('127.0.0.1', 'api.live.bilibili.com', 'new-live-cdn.example', 'new-upos.example', 'api.xiaomimimo.com'):
        assert proxy_bypass(host)
        assert requests.utils.get_environ_proxies('https://' + host + '/media') == {}
    assert all(not os.environ.get(k) for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'))


def test_aiohttp_and_httpx_obey_direct_policy(poisoned_proxy):
    use_direct_network()
    from aiohttp.helpers import get_env_proxy_for_url
    from yarl import URL
    with pytest.raises(LookupError):
        get_env_proxy_for_url(URL('https://api.live.bilibili.com/'))
    import httpx
    with httpx.Client() as client:
        assert not any(transport is not None for transport in client._mounts.values())


def test_uploader_session_does_not_inherit_system_proxy(poisoned_proxy):
    client = BilibiliRuntimeClient(cookies={'bili_jct': 'isolated-test'})
    assert not client.session.trust_env
    supplied = requests.Session()
    client = BilibiliRuntimeClient(cookies={'bili_jct': 'isolated-test'}, session=supplied)
    assert client.session is supplied and not supplied.trust_env


