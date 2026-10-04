"""Loopback Worker API and owned independent upload process."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from bilive.db.conn import open_connection


class ConsumerProcess:
    def __init__(self, settings, db_path: Path, runtime: Path, project: Path, popen=subprocess.Popen):
        self.settings, self.db_path, self.runtime, self.project = settings, db_path, runtime, project
        self.popen, self.process = popen, None
        self.enabled = os.environ.get("BILIVE_AUTO_UPLOAD", str(settings.upload.auto_start)).lower() in {"1","true","yes"}
        self.reason = "disabled" if not self.enabled else "not started"
        self.last_exit = None

    def eligible(self):
        conn = open_connection(self.db_path, readonly=True)
        try:
            return bool(conn.execute("SELECT 1 FROM upload_queue WHERE stage IN ('queued','uploaded','uploading','publishing') LIMIT 1").fetchone())
        finally:
            conn.close()

    def tick(self):
        if self.process is not None:
            result = self.process.poll()
            if result is None:
                return
            self.last_exit = result
            self.process = None
            self.reason = "consumer exited; explicit restart required"
            # Do not respawn a broken child on every scheduler tick.
            self.enabled = False
        if not self.enabled or not self.eligible():
            return
        cookie = self.settings.paths.cookie_file or self.project / ".secrets/bilibili.cookie"
        if not cookie.is_file():
            self.reason = "Cookie file unavailable"
            return
        if importlib.util.find_spec("bilitool") is None:
            self.reason = "pinned bilitool dependency unavailable"
            return
        env = os.environ.copy()
        env["BILIVE_DB_PATH"] = str(self.db_path.resolve())
        env["BILIVE_COOKIE_FILE"] = str(cookie.resolve())
        env["BILIVE_RUNTIME_DIR"] = str(self.runtime.resolve())
        env["BILIVE_DIR"] = str(self.project.resolve())
        (self.runtime / "upload-stop").unlink(missing_ok=True)
        self.process = self.popen([sys.executable,str(self.project / "scripts/start_upload_consumer.py")],
            cwd=self.project,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.reason = "running"

    def busy(self):
        return self.enabled and self.eligible()

    def status(self):
        running = self.process is not None and self.process.poll() is None
        result = {"state":"running" if running else "offline", "detail":self.reason,"last_exit":self.last_exit}
        if running:
            path = self.runtime / "upload-status.json"
            if path.is_file():
                try:
                    result["progress"] = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError,OSError):
                    result["progress"] = {"status":"unavailable"}
        return result

    def stop(self):
        # Consumer polls this file between cycles; current network work finishes.
        self.enabled = False
        if self.process is not None and self.process.poll() is None:
            self.runtime.mkdir(parents=True,exist_ok=True)
            (self.runtime / "upload-stop").touch()

    def start(self):
        self.enabled = True
        (self.runtime / "upload-stop").unlink(missing_ok=True)
        self.tick()
        return self.status()


def create_worker_app(worker, consumer):
    app = FastAPI(title="Bilive local Worker")

    @app.middleware("http")
    async def local_origin(request, call_next):
        origin = request.headers.get("origin")
        if request.method != "GET" and origin and origin != f"http://{request.headers.get('host')}":
            return JSONResponse({"detail":"cross-origin Worker control denied"},status_code=403)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"service":"bilive-worker","state":"stopping" if worker.stop_event.is_set() else "running",
                "busy":worker.jobs.busy(),"uploader":consumer.status()}

    @app.post("/stop")
    def stop():
        worker.stop()
        consumer.stop()
        return {"accepted":True,"state":"stopping"}

    @app.post("/uploader/start")
    def start_uploader():
        return consumer.start()

    @app.post("/uploader/stop")
    def stop_uploader():
        consumer.stop()
        return {"accepted":True,"state":"stopping"}

    return app


def serve(worker, settings, consumer, port: int):
    import uvicorn
    worker.on_tick = consumer.tick
    worker.external_busy = consumer.busy
    server = uvicorn.Server(uvicorn.Config(create_worker_app(worker,consumer),host="127.0.0.1",port=port,log_level="warning"))
    errors = []
    def run():
        try:
            worker.run_forever(settings.worker.idle_timeout,settings.worker.idle_check_interval)
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            consumer.stop()
            server.should_exit = True
    thread = threading.Thread(target=run,daemon=False)
    thread.start()
    try:
        server.run()
    finally:
        worker.stop()
        thread.join()
    if errors:
        raise RuntimeError("Worker startup/execution failed: " + errors[0])
