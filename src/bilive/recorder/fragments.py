"""Keep brief recorder stream fragments out of the recording library."""
from __future__ import annotations

import json
import math
import subprocess
from functools import lru_cache
from pathlib import Path

from bilive.media.process import run_media_command

MAX_FRAGMENT_BYTES = 10 * 1024 * 1024
MIN_RECORDING_SECONDS = 30


@lru_cache(maxsize=256)
def _short_duration(path: str, size: int, modified: int) -> bool:
    try:
        probe = run_media_command(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'json', path], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=10)
        if probe.returncode:
            return False  # An unreadable file needs separate recovery, not guessing.
        duration = float(json.loads(probe.stdout)['format']['duration'])
        return math.isfinite(duration) and 0 < duration < MIN_RECORDING_SECONDS
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        return False


def is_recording_fragment(source: Path) -> bool:
    stat = source.stat()
    return (0 < stat.st_size < MAX_FRAGMENT_BYTES
            and _short_duration(str(source.resolve()), stat.st_size, stat.st_mtime_ns))


def quarantine_fragment(videos: Path, source: Path) -> Path:
    """Move a completed fragment and its sidecars outside Videos; retain recovery."""
    root = videos.resolve()
    source = source.resolve()
    rel = source.relative_to(root)
    destination = root.parent / 'artifacts' / 'recording-fragments' / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    related = [source, *source.parent.glob(source.stem + '.*')]
    files = list(dict.fromkeys(p for p in related if p.is_file()))
    if any((destination.parent / p.name).exists() for p in files):
        raise FileExistsError('fragment quarantine destination already exists')
    for path in files:
        path.rename(destination.parent / path.name)
    return destination
