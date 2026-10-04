"""Load the existing ignored .secrets/env format, preserving process overrides."""
import os
from pathlib import Path


def load_secret_env(path: Path) -> None:
    if not path.is_file():
        return
    existing = set(os.environ)
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if name:
            values[name] = value
    for name, value in values.items():
        if name not in existing:
            os.environ[name] = value


def redact(message: str) -> str:
    for name, value in os.environ.items():
        if value and len(value) >= 4 and any(word in name.upper() for word in ("KEY", "TOKEN", "COOKIE", "PASSWORD", "SECRET")):
            message = message.replace(value, "[redacted]")
    return message
