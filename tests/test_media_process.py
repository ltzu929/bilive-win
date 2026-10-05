import os
import sys

import pytest

from bilive.media.process import run_media_command


@pytest.mark.skipif(os.name != "nt", reason="Windows console behavior")
def test_background_media_child_has_no_console():
    result = run_media_command(
        [sys.executable, "-c", "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "0"
