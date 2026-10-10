"""Read public Bilibili room covers directly; never forward local credentials."""
from functools import lru_cache
from hashlib import sha256
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import re
from urllib.parse import urlsplit, urlunsplit

import requests


class CoverUnavailable(Exception):
    pass


_MIME_SUFFIXES = {'image/jpeg': '.jpg', 'image/png': '.png',
                  'image/webp': '.webp', 'image/gif': '.gif'}


def cover_cache_dir() -> Path:
    return Path(os.environ.get('BILIVE_RUNTIME_DIR', '.runtime')) / 'room-covers'


def _read_disk_cover(key: str):
    for mime, suffix in _MIME_SUFFIXES.items():
        path = cover_cache_dir() / (key + suffix)
        try:
            if 0 < path.stat().st_size <= 2 * 1024 * 1024:
                return path.read_bytes(), mime
        except OSError:
            continue
    return None


def _save_disk_cover(key: str, body: bytes, mime: str):
    directory = cover_cache_dir()
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (key + _MIME_SUFFIXES[mime])
        # Publish complete files so concurrent requests cannot read a partial write.
        with NamedTemporaryFile(dir=directory, suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            file.write(body)
        temporary.replace(path)
        # Bound persistent storage just like the memory cache (at most 64 MiB).
        files = sorted((p for p in directory.iterdir() if p.suffix in _MIME_SUFFIXES.values()),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[32:]:
            old.unlink(missing_ok=True)
    except OSError:
        # A read-only/full disk must not prevent displaying a downloaded image.
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
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
    # The digest only maps a CDN URL to a safe fixed-length filename.
    key = sha256(url.encode('utf-8')).hexdigest()
    cached = _read_disk_cover(key)
    if cached is not None:
        return cached
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
                body = b''.join(chunks)
                _save_disk_cover(key, body, mime)
                return body, mime
    except requests.RequestException:
        raise CoverUnavailable('直播封面连接失败') from None
