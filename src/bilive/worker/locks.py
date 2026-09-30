"""跨进程文件锁（规格 §5：线程锁不能替代跨进程互斥）。"""

from __future__ import annotations

import os
import time
from pathlib import Path


class LockUnavailable(Exception):
    pass


class ProcessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def acquire(self, timeout: float = 0.0) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + timeout
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                self._fh = True
                return
            except FileExistsError:
                if time.time() >= deadline:
                    raise LockUnavailable(f"lock held: {self.path}") from None
                time.sleep(0.05)

    def release(self) -> None:
        if self._fh:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            self._fh = None

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
