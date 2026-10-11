"""Dashboard operations against the local Worker, with explicit write-side wake."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from bilive.worker.locks import ProcessLock


class WorkerControl:
    def __init__(self, project: Path, runtime: Path, port: int = 2235, python: Path | None = None):
        self.project, self.runtime, self.port = project, runtime, port
        self.process = None
        self.python = str(python) if python else sys.executable

    def request(self, path: str, write: bool = False):
        request = Request(f"http://127.0.0.1:{self.port}{path}", data=b"" if write else None,
                          method="POST" if write else "GET")
        with urlopen(request,timeout=1) as response:
            result = json.loads(response.read())
        return result

    def status(self):
        try:
            status = self.request("/health")
            if status.get("service") != "bilive-worker":
                return {"state":"unavailable","detail":"unexpected service on Worker port"}
            return status
        except (URLError, OSError, ValueError):
            return {"state":"paused" if (self.runtime / 'worker-paused').exists() else "offline"}

    def wake(self):
        if (self.runtime / 'worker-paused').exists():
            return {'state': 'paused', 'detail': 'Worker paused by user'}
        with ProcessLock(self.runtime / "worker-start.lock"):
            status = self.status()
            if status["state"] != "offline":
                return status
            if self.process is not None and self.process.poll() is None:
                return {"state":"starting"}
            env = os.environ.copy()
            env["PYTHONPATH"] = str(self.project / "src")
            env["BILIVE_RUNTIME_DIR"] = str(self.runtime.resolve())
            self.process = subprocess.Popen([self.python,str(self.project / "scripts/start_worker.py"),"--serve","--port",str(self.port)],
                cwd=self.project,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            return {"state":"starting"}

    def stop(self):
        status = self.status()
        if status["state"] in {"offline", "paused"}:
            return status
        if status.get("service") != "bilive-worker":
            raise ValueError("unexpected service on Worker port")
        return self.request("/stop",write=True)

    def uploader(self, action: str):
        if action not in {'start', 'stop'}:
            raise ValueError('invalid uploader action')
        status = self.status()
        if status.get('service') != 'bilive-worker':
            raise ValueError('Worker must be running to control uploader')
        return self.request(f'/uploader/{action}', write=True)
