"""Pinned baseline secret Cookie injection; never persist runtime Cookie.
Source: b2e2f10 src/server/recorder_server.py; see vendor/PROVENANCE.md.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any

def read_secret_cookie(cookie_path: str | Path) -> str:
    path = Path(cookie_path)
    if not path.is_file():
        return ""
    cookie = path.read_text(encoding="utf-8-sig").strip()
    if "\n" in cookie or "\r" in cookie:
        raise ValueError("bilibili.cookie must contain one HTTP Cookie header line")
    if cookie and "=" not in cookie:
        raise ValueError("bilibili.cookie is not a valid HTTP Cookie header")
    return cookie

def persistent_settings_payload(settings: Any) -> dict[str, Any]:
    """Build the TOML payload while excluding the in-memory secret Cookie."""
    payload = settings.dict(exclude_none=True)
    payload.setdefault("header", {})["cookie"] = ""
    return payload

def install_secret_cookie(settings_cookie: str) -> bool:
    """Inject Cookie into blrec's Settings.load and strip it from every dump."""
    if not settings_cookie:
        return False

    import toml
    from blrec.setting import Settings

    original_load = Settings.load.__func__

    @classmethod
    def load_with_secret(cls: type[Any], path: str) -> Any:
        settings = original_load(cls, path)
        settings.header.cookie = settings_cookie
        return settings

    def dump_without_secret(settings: Any) -> None:
        settings_path = Path(settings._path)
        serialized = toml.dumps(persistent_settings_payload(settings))
        temporary = settings_path.with_suffix(settings_path.suffix + ".bilive-tmp")
        temporary.write_text(serialized, encoding="utf8", newline="")
        os.replace(temporary, settings_path)

    Settings.load = load_with_secret
    Settings.dump = dump_without_secret
    return True
