"""Read public Bilibili room covers directly; never forward local credentials."""
from functools import lru_cache
import re
from urllib.parse import urlsplit, urlunsplit

import requests


class CoverUnavailable(Exception):
    pass


@lru_cache(maxsize=32)
def read_cover(url: str) -> tuple[bytes, str]:
    # Only recorder-supplied public CDN images are supported. No arbitrary URL proxy.
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or
            not re.fullmatch(r'i\d+\.hdslb\.com', parsed.hostname or '') or
            parsed.port not in (None, 443) or parsed.username or parsed.password or
            not parsed.path.startswith('/bfs/')):
        raise CoverUnavailable('直播封面地址不可用')
    try:
        # CDN-resized cover is enough for the 420px panel at 2x pixel density.
        image_url = urlunsplit(parsed._replace(path=parsed.path + '@840w_472h_1c.webp'))
        with requests.Session() as session:
            session.trust_env = False
            with session.get(image_url, timeout=(5, 10), stream=True, allow_redirects=False) as response:
                mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                if response.status_code != 200 or mime not in {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}:
                    raise CoverUnavailable('直播封面读取失败')
                chunks, size = [], 0
                for chunk in response.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise CoverUnavailable('直播封面文件过大')
                    chunks.append(chunk)
                if not size:
                    raise CoverUnavailable('直播封面为空')
                return b''.join(chunks), mime
    except requests.RequestException:
        raise CoverUnavailable('直播封面连接失败') from None
