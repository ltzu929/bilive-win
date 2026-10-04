"""Windows undoable recycle adapter from pinned legacy baseline b2e2f10.

Source: src/dashboard/recording_trash.py; see vendor/PROVENANCE.md and LICENSE.
"""
import ctypes
import os
from pathlib import Path

def move_to_recycle_bin(paths: list[Path]) -> None:
    """Use SHFileOperationW with FOF_ALLOWUNDO; never permanently delete."""
    if os.name != "nt":
        raise RuntimeError("Windows Recycle Bin is only available on Windows")
    existing = [path for path in paths if path.is_file()]
    if len(existing) != len(paths):
        raise FileNotFoundError("source package changed before recycle-bin move")
    if not existing:
        raise FileNotFoundError("source package is empty")

    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.UINT),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    # FO_DELETE + FOF_ALLOWUNDO is the shell API's undoable delete.
    source_buffer = ctypes.create_unicode_buffer(
        "\0".join(str(path) for path in existing) + "\0\0"
    )
    operation = SHFILEOPSTRUCTW(
        None,
        0x0003,
        ctypes.cast(source_buffer, wintypes.LPCWSTR),
        None,
        0x0040 | 0x0010 | 0x0004 | 0x0400,
        False,
        None,
        None,
    )
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(f"SHFileOperationW failed with code {result}")
