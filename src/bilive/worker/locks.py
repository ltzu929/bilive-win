"""跨进程文件锁（规格 §5：线程锁不能替代跨进程互斥）。"""

from __future__ import annotations

import os
import time
from pathlib import Path


class LockUnavailable(Exception):
    pass


class ProcessLock:
    def __init__(self, path: Path, create: bool = True) -> None:
        self.path = path
        self.create = create
        self._fh = None

    def acquire(self, timeout: float = 0.0) -> None:
        if self.create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # Do not append/initialize before locking: on Windows another process may
        # already own byte zero while this process observes an empty new file.
        flags = os.O_RDWR | (os.O_CREAT if self.create else 0) | getattr(os, 'O_BINARY', 0)
        fh = os.fdopen(os.open(self.path, flags, 0o600), 'r+b', buffering=0)
        deadline = time.time() + timeout
        while True:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                if self.create and os.fstat(fh.fileno()).st_size == 0:
                    fh.write(b'0')
                self._fh = fh
                return
            except OSError:
                if time.time() >= deadline:
                    fh.close()
                    raise LockUnavailable(f"lock held: {self.path}") from None
                time.sleep(0.05)

    def release(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def __enter__(self) -> "ProcessLock":
        self.acquire(timeout=2)
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
