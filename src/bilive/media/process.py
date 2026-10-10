"""Run media tools without a console or interactive stdin in background jobs."""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path


# Candidate preparation runs in parallel; local transcoding must not multiply
# that concurrency or let each decoder/encoder use every CPU core.
_ffmpeg_slot = threading.BoundedSemaphore(1)
MEDIA_THREADS = 4


def _bounded_ffmpeg_command(command: list[str]) -> list[str]:
    options = ["-threads", str(MEDIA_THREADS)]
    result = [command[0], "-filter_threads", str(MEDIA_THREADS),
              "-filter_complex_threads", str(MEDIA_THREADS)]
    for argument in command[1:-1]:
        if argument == "-i":
            result.extend(options)  # Decoder option belongs before its input.
        result.append(argument)
    # Encoder option belongs before the output, after any codec selection.
    result.extend(options)
    result.append(command[-1])
    return result


def run_media_command(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
             if os.name == "nt" else 0)
    if Path(command[0]).stem.lower() == "ffmpeg":
        with _ffmpeg_slot:
            return subprocess.run(_bounded_ffmpeg_command(command),
                                  stdin=subprocess.DEVNULL, creationflags=flags, **kwargs)
    return subprocess.run(command, stdin=subprocess.DEVNULL, creationflags=flags, **kwargs)
