"""Run media tools without a console or interactive stdin in background jobs."""
from __future__ import annotations

import os
import subprocess


def run_media_command(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        **kwargs,
    )
