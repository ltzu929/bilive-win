import os
import sys
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import shutil

import pytest

from bilive.media.process import run_media_command


@pytest.mark.skipif(os.name != "nt", reason="Windows console behavior")
def test_background_media_child_has_no_console():
    result = run_media_command(
        [sys.executable, "-c", "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "0"


@pytest.mark.skipif(os.name != "nt", reason="Windows process priority")
def test_media_child_yields_to_desktop():
    result = run_media_command(
        [sys.executable, "-c", "import ctypes; from ctypes import wintypes; k=ctypes.windll.kernel32; k.GetCurrentProcess.restype=wintypes.HANDLE; k.GetPriorityClass.argtypes=[wintypes.HANDLE]; print(k.GetPriorityClass(k.GetCurrentProcess()))"],
        capture_output=True, text=True, check=True,
    )
    assert int(result.stdout.strip()) == subprocess.BELOW_NORMAL_PRIORITY_CLASS


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_parallel_requests_serialize_real_ffmpeg(monkeypatch):
    import bilive.media.process as media

    original = subprocess.run
    guard = threading.Lock()
    active = peak = 0
    commands = []

    def observed_run(command, **kwargs):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
            commands.append(command)
        try:
            return original(command, **kwargs)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(media.subprocess, "run", observed_run)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", "testsrc=duration=0.5:size=64x64:rate=10", "-c:v", "libx264",
               "-f", "null", "-"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: run_media_command(command, capture_output=True, check=True), range(3)))
    assert len(results) == 3
    assert peak == 1
    for actual in commands:
        assert actual[actual.index("-i") - 2:actual.index("-i")] == ["-threads", "4"]
        assert actual[-3:] == ["-threads", "4", "-"]
