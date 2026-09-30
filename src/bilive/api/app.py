"""Dashboard 轻接口：查询、草稿、提交 job，不在请求内执行重处理（规格 §6）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bilive.config.settings import load_settings
from bilive.db.conn import DatabaseUnavailable, open_connection, require_table
from bilive.domain.models import (
    SubtitleLine,
    clamp_subtitle_times,
    segment_id_from_source_and_range,
    task_id_from_source_relpath,
)
from bilive.domain.states import manual_retry_allowed
from bilive.jobs.action_jobs import ActionConflict, ActionJobStore
from bilive.media.subtitle_store import SubtitleStore

app = FastAPI(title="Bilive Workbench API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("BILIVE_DASHBOARD_ALLOWED_HOSTS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

settings = load_settings()
jobs = ActionJobStore(Path(".bilive-jobs"))
subtitles = SubtitleStore(Path("artifacts/subtitles"))


class ApiError(BaseModel):
    detail: str
    code: str


class SegmentEdit(BaseModel):
    source_relpath: str
    source_start: float = Field(ge=0)
    source_end: float = Field(ge=0)
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    revision: int = 1

    def validate_range(self) -> None:
        if self.source_end <= self.source_start:
            raise HTTPException(status_code=422, detail="source_end must be > source_start")


class SubtitleDraft(BaseModel):
    segment_id: str
    duration: float = Field(gt=0)
    revision: int = 1
    expected_revision: int | None = None
    lines: list[dict[str, Any]]


class ActionRequest(BaseModel):
    action: str
    target: str
    payload: dict[str, Any] = Field(default_factory=dict)
    revision: int | None = None


class UploadRetryRequest(BaseModel):
    video_path: str
    stage: str
    remote_filename: str | None = None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "bilive-workbench"}


@app.get("/api/recordings")
def list_recordings(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    root = settings.videos_root()
    if not root.is_dir():
        return {"items": [], "total": 0, "limit": limit, "offset": offset}
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.mp4")):
        rel = path.relative_to(root).as_posix()
        items.append(
            {
                "task_id": task_id_from_source_relpath(rel),
                "source_relpath": rel,
                "size": path.stat().st_size,
            }
        )
    total = len(items)
    return {
        "items": items[offset : offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/segments/{segment_id}")
def get_segment(segment_id: str) -> dict[str, Any]:
    # 轻接口只读展示；完整检索走 workbench 索引
    return {"segment_id": segment_id, "revision": 1, "subtitles": [], "artifacts": []}


@app.post("/api/segments/point")
def set_segment_points(edit: SegmentEdit) -> dict[str, Any]:
    edit.validate_range()
    settings.ensure_within_videos(Path(edit.source_relpath))
    segment_id = segment_id_from_source_and_range(
        edit.source_relpath,
        int(edit.source_start * 1000),
        int(edit.source_end * 1000),
    )
    return {
        "segment_id": segment_id,
        "revision": edit.revision + 1,
        "source_start": edit.source_start,
        "source_end": edit.source_end,
    }


@app.post("/api/subtitles/save")
def save_subtitles(draft: SubtitleDraft) -> dict[str, Any]:
    if not draft.lines:
        raise HTTPException(status_code=422, detail="at least one subtitle line")
    lines = [
        SubtitleLine(
            index=i + 1,
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item["text"]),
        )
        for i, item in enumerate(draft.lines)
    ]
    try:
        saved = subtitles.save(
            draft.segment_id,
            draft.duration,
            lines,
            draft.revision,
            expected_revision=draft.expected_revision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # 保存字幕 ≠ 更新成片；调用方必须再提交 reburn/finalize
    return {
        "segment_id": draft.segment_id,
        "revision": saved["revision"],
        "lines": len(saved["lines"]),
        "requires_reburn": True,
    }


@app.post("/api/actions")
def submit_action(req: ActionRequest) -> dict[str, Any]:
    allowed = {
        "finalize",
        "render",
        "reburn",
        "retry_judge",
        "approve_publish",
        "manual_keep",
        "finish_review",
        "trash_recycle",
        "retry_upload",
        "start_analysis",
    }
    if req.action not in allowed:
        raise HTTPException(status_code=422, detail=f"unknown action: {req.action}")
    try:
        job = jobs.submit(req.action, req.target, {**req.payload, "revision": req.revision})
    except ActionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job.job_id, "status": job.status.value, "action": job.action}


@app.get("/api/actions/{job_id}")
def get_action(job_id: str) -> dict[str, Any]:
    for status in ("pending", "processing", "done", "failed"):
        path = Path(".bilive-jobs") / f"{job_id}.{status}.json"
        if path.is_file():
            import json

            return json.loads(path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="job not found")


@app.get("/api/uploads")
def list_uploads(
    stage: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    db_path = settings.paths.db_path or Path("src/db/data.db")
    try:
        conn = open_connection(db_path, readonly=True)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "unavailable", "detail": str(exc)}) from exc
    try:
        require_table(conn, "upload_queue")
        where = "WHERE stage != 'staged'" if stage is None else "WHERE stage = ?"
        params: list[Any] = [] if stage is None else [stage]
        total = conn.execute(f"SELECT COUNT(*) FROM upload_queue {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM upload_queue {where} ORDER BY id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        conn.close()


@app.post("/api/uploads/retry")
def retry_upload(req: UploadRetryRequest) -> dict[str, Any]:
    if not manual_retry_allowed(req.stage, req.remote_filename):
        raise HTTPException(
            status_code=422,
            detail="manual retry only for failed rows without remote_filename",
        )
    job = jobs.submit("retry_upload", req.video_path, {"stage": req.stage})
    return {"job_id": job.job_id, "status": job.status.value}


@app.get("/api/services")
def service_status() -> dict[str, Any]:
    return {
        "recorder": {"state": "unknown", "detail": "blrec not probed in skeleton"},
        "workbench": {"state": "ok", "detail": "interface service"},
        "worker": {"state": "unknown", "detail": "worker not probed"},
        "uploader": {"state": "unknown", "detail": "uploader not probed"},
    }


# 工作台静态页（设计稿六页面）
_FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIR), name="frontend-assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_FRONTEND_DIR / "index.html")
