"""Keep Bilive traffic direct without changing the user's system proxy."""
from __future__ import annotations

import os


def use_direct_network() -> None:
    # Upstream requests/aiohttp/httpx also inspect Windows proxy settings.
    # NO_PROXY=* bypasses those settings, including URLs not known in advance
    # (live CDN and UPOS hosts). Apply after loading the local secret env.
    for name in tuple(os.environ):
        if name.lower() in {'http_proxy', 'https_proxy', 'all_proxy'}:
            os.environ.pop(name, None)
    os.environ['NO_PROXY'] = '*'
    os.environ['no_proxy'] = '*'
