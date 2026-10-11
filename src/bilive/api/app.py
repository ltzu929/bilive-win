"""Dashboard 轻接口：查询、草稿、提交 job，不在请求内执行重处理（规格 §6）。"""

from __future__ import annotations

import os
import ipaddress
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool

from bilive.config.settings import load_settings
from bilive.db.conn import DatabaseUnavailable, open_connection, require_table
from bilive.domain.models import (
    SubtitleLine,
    ActionJobStatus,
    task_id_from_source_relpath,
)
from bilive.domain.states import manual_retry_allowed
from bilive.jobs.action_jobs import ActionConflict, ActionJobStore
from bilive.media.subtitle_store import SubtitleStore
from bilive.media.workbench import Workbench, validate_id
from bilive.worker.locks import LockUnavailable
from bilive.worker.control import WorkerControl
from bilive.domain.models import source_relpath_from_task_id
from bilive.upload.queue import UploadQueue
from bilive.lifecycle.review import SourceReview
from bilive.recorder.client import RecorderClient, RecorderUnavailable
from bilive.config.secrets import load_secret_env
from bilive.network import use_direct_network

load_secret_env(Path('.secrets/env'))
use_direct_network()
settings = load_settings()
app = FastAPI(title="Bilive Workbench API", version="2.0.0")


@app.middleware('http')
async def same_origin_writes(request, call_next):
    try:
        host=(urlsplit('//'+request.headers.get('host','')).hostname or '').lower()
        permitted = host in {'localhost','test','testserver'} or host in settings.dashboard.allowed_hosts
        if not permitted:
            permitted = ipaddress.ip_address(host).is_private
    except ValueError:
        permitted = False
    if not permitted:
        return JSONResponse(status_code=400,content={'detail':'Invalid dashboard host'})
    origin = request.headers.get('origin')
    expected = f'{request.url.scheme}://{request.headers.get("host", "")}'
    if (request.method not in {'GET', 'HEAD', 'OPTIONS'} and origin
            and origin != expected):
        return JSONResponse(status_code=403, content={'detail':'cross-origin write rejected'})
    return await call_next(request)

recorder_client = RecorderClient(int(os.environ.get('BILIVE_RECORDER_PORT', '2233')))
jobs = ActionJobStore(Path(os.environ.get("BILIVE_JOBS_DIR", ".bilive-jobs")))
artifacts_root = Path(os.environ.get("BILIVE_ARTIFACTS_DIR", "artifacts"))
subtitles = SubtitleStore(artifacts_root / "subtitles")
upload_queue = UploadQueue(settings.paths.db_path or Path("src/db/data.db"))
workbench = Workbench(settings.videos_root(), artifacts_root, jobs, subtitles, upload_queue,
    settings.subtitle_style())
workbench.default_style_provider = lambda: load_settings(settings.paths.config_path).subtitle_style()
from bilive.lifecycle.runtime import configure_workbench
configure_workbench(workbench, settings.paths.config_path, recorder_client)
worker_control = WorkerControl(Path(__file__).resolve().parents[3],
    Path(os.environ.get("BILIVE_RUNTIME_DIR", ".runtime")),int(os.environ.get("BILIVE_WORKER_PORT","2235")))


def accepted_with_wake(result):
    # Persisted job is the durable result; launch failure does not roll it back.
    try:
        result["worker"] = worker_control.wake()
    except (OSError, LockUnavailable):
        result["worker"] = {"state":"unavailable","detail":"wake failed; task retained"}
    return result


@app.exception_handler(ValueError)
async def invalid_request(request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(FileNotFoundError)
async def missing_object(request, exc):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ActionConflict)
async def conflicting_request(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(LockUnavailable)
async def busy_segment(request, exc):
    return JSONResponse(status_code=409, content={"detail": "后台正在更新片段数据，请稍后再保存"})


@app.exception_handler(DatabaseUnavailable)
async def unavailable_database(request, exc):
    return JSONResponse(status_code=503, content={"detail": "upload database unavailable"})


@app.exception_handler(RecorderUnavailable)
async def unavailable_recorder(request, exc):
    return JSONResponse(status_code=503, content={'detail':str(exc)})


@app.get('/api/rooms')
def list_rooms(page: int = Query(1, ge=1), size: int = Query(20, ge=10, le=100)):
    return {'items':recorder_client.rooms(page, size), 'page':page, 'size':size}


@app.post('/api/rooms/{room_id}/{action}')
def control_room(room_id: int, action: str):
    return recorder_client.control(room_id, action)


@app.get('/api/rooms/{room_id}/cover')
def room_cover(room_id: int):
    from bilive.recorder.cover import CoverUnavailable, read_cover
    if room_id <= 0:
        raise HTTPException(status_code=422, detail='invalid room_id')
    data = recorder_client.request(f'/{room_id}/data')
    url = data.get('room_info', {}).get('cover')
    if not url:
        raise HTTPException(status_code=404, detail='该直播间暂无封面')
    try:
        body, mime = read_cover(url)
    except CoverUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return Response(body, media_type=mime, headers={
        'Cache-Control': 'private, max-age=300', 'X-Content-Type-Options': 'nosniff'})


@app.post('/api/recordings/{task_id}/task/{action}')
def control_recording_task(task_id: str, action: str):
    from bilive.analysis.tasks import RecordingTasks
    validate_id(task_id)
    if action == 'queue':
        job = RecordingTasks(workbench).queue(task_id)
        return accepted_with_wake({'job_id':job.job_id,'status':job.status.value})
    return RecordingTasks(workbench).control(task_id, action)


class ApiError(BaseModel):
    detail: str
    code: str


class SegmentEdit(BaseModel):
    segment_id: str | None = None
    expected_revision: int | None = None
    note: str = ""
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
    lines: list["TimedSubtitle"]


class TimedSubtitle(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str


SubtitleDraft.model_rebuild()


class ActionRequest(BaseModel):
    action: str
    target: str
    payload: dict[str, Any] = Field(default_factory=dict)
    revision: int | None = None


class UploadRetryRequest(BaseModel):
    video_path: str
    stage: str
    remote_filename: str | None = None


class SourceReviewRequest(BaseModel):
    no_content: bool = False
    note: str = ''


class DropRequest(BaseModel):
    expected_revision: int
    reason: str


class StyleRequest(BaseModel):
    expected_revision: int
    style: dict[str, Any]


class KeepRequest(BaseModel):
    expected_revision: int


@app.post('/api/segments/{segment_id}/keep')
def keep_segment(segment_id: str, request: KeepRequest):
    return workbench.keep(segment_id, request.expected_revision)


class MetadataRequest(BaseModel):
    expected_revision: int
    title: str
    description: str = ''
    tags: list[str] = Field(default_factory=list)
    tid: int | None = Field(default=None, ge=1, le=10000)


@app.post('/api/segments/{segment_id}/metadata')
def save_segment_metadata(segment_id: str, request: MetadataRequest):
    return workbench.save_metadata(segment_id, request.expected_revision,
                                   request.title, request.description, request.tags, request.tid)


class PreviewRequest(BaseModel):
    expected_revision: int
    ranges: list[list[float]]


@app.post('/api/segments/{segment_id}/preview')
def record_final_preview(segment_id: str, request: PreviewRequest):
    return workbench.record_preview(segment_id, request.expected_revision, request.ranges)


@app.get('/api/segments/{segment_id}/cover')
def default_final_cover(segment_id: str):
    segment = workbench.load(segment_id)
    path = Path(segment.get('cover_path') or '')
    expected = workbench.artifacts / segment_id / 'cover.jpg'
    if path.resolve() != expected.resolve() or not path.is_file():
        raise FileNotFoundError('当前成片暂无默认封面')
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'no-cache'})


class RetainRequest(BaseModel):
    keep: StrictBool


@app.post('/api/recordings/{task_id}/retain')
def retain_recording(task_id: str, request: RetainRequest):
    return {'record': SourceReview(workbench).retain(task_id, request.keep)}


@app.post('/api/segments/{segment_id}/style')
def save_segment_style(segment_id: str, request: StyleRequest):
    return workbench.save_style(segment_id,request.expected_revision,request.style)


@app.get('/api/recordings/{task_id}/lifecycle')
def get_lifecycle(task_id: str):
    return {'record': SourceReview(workbench).view(task_id)}


@app.get('/api/lifecycle')
def list_lifecycle(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    service = SourceReview(workbench)
    tasks = {p.stem for p in service.root.glob('*.json')}
    protected = service.protected()
    tasks.update(task_id_from_source_relpath(p.relative_to(workbench.videos).as_posix())
                 for p in workbench.videos.rglob('*.mp4') if p.resolve() not in protected)
    records = [service.view(task) for task in tasks]
    records.sort(key=lambda r:r['retention_started_at'], reverse=True)
    return {'total':len(records), 'items':records[offset:offset+limit]}


@app.post('/api/recordings/{task_id}/finish-review')
def finish_review(task_id: str, request: SourceReviewRequest):
    result = SourceReview(workbench).finish(task_id, request.no_content, request.note)
    return accepted_with_wake(result) if result['recycle_queued'] else result


@app.post('/api/recordings/{task_id}/recycle')
def queue_recycle(task_id: str):
    return accepted_with_wake(SourceReview(workbench).queue(task_id))


@app.post('/api/segments/{segment_id}/drop')
def drop_segment(segment_id: str, request: DropRequest):
    return workbench.drop(segment_id, request.expected_revision, request.reason)


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
        from bilive.recorder.fragments import is_recording_fragment
        if is_recording_fragment(path):
            continue
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
    return workbench.detail(segment_id)


@app.get("/api/recordings/{task_id}/segments")
def recording_segments(task_id: str) -> dict:
    validate_id(task_id)
    rel = source_relpath_from_task_id(task_id)
    workbench.source(rel)
    related = [(path,job) for state in ActionJobStatus for path,job in jobs.valid_jobs(state)
               if job.target == task_id and job.action == "start_analysis"]
    newest = max(related, key=lambda pair:pair[0].stat().st_mtime_ns)[1] if related else None
    return {"items": workbench.list(rel), "source_relpath": rel,
            "auto_analysis_enabled":settings.analysis.auto_start is True,
            "analysis_job":({"job_id":newest.job_id,"status":newest.status.value,
                            "progress":newest.progress,"result":newest.result} if newest else None)}


@app.get('/api/recordings/{task_id}/evidence')
def get_recording_evidence(task_id: str,limit: int=Query(20,ge=1,le=100),offset: int=Query(0,ge=0)):
    from bilive.media.evidence import recording_evidence
    validate_id(task_id)
    return recording_evidence(workbench,task_id,limit,offset)


@app.get("/api/media/source/{task_id}")
def source_media(task_id: str):
    validate_id(task_id)
    return FileResponse(workbench.source(source_relpath_from_task_id(task_id)), media_type="video/mp4")


@app.get("/api/media/final/{segment_id}")
def final_media(segment_id: str):
    s = workbench.load(segment_id)
    if not workbench.valid_final(s):
        raise HTTPException(status_code=409, detail="current final is invalid; regenerate first")
    path = Path(s["final_path"]).resolve()
    if not path.is_relative_to(workbench.artifacts):
        raise HTTPException(status_code=422, detail="artifact outside root")
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/segments/point")
def set_segment_points(edit: SegmentEdit) -> dict[str, Any]:
    edit.validate_range()
    return workbench.points(edit.source_relpath, edit.source_start, edit.source_end,
                            edit.title, edit.note, edit.segment_id, edit.expected_revision)


@app.post("/api/subtitles/save")
def save_subtitles(draft: SubtitleDraft) -> dict[str, Any]:
    if not draft.lines:
        raise HTTPException(status_code=422, detail="at least one subtitle line")
    lines = [
        SubtitleLine(
            index=i + 1,
            start=item.start,
            end=item.end,
            text=item.text,
        )
        for i, item in enumerate(draft.lines)
    ]
    if draft.expected_revision is None:
        raise HTTPException(status_code=422, detail="expected_revision required")
    saved = workbench.save_subtitles(draft.segment_id, draft.expected_revision, lines)
    return {**saved, "requires_reburn": True}


@app.post("/api/actions")
def submit_action(req: ActionRequest) -> dict[str, Any]:
    if req.action == "start_analysis":
        validate_id(req.target)
        workbench.source(source_relpath_from_task_id(req.target))
        from bilive.analysis.tasks import RecordingTasks
        job = RecordingTasks(workbench).queue(req.target)
        return accepted_with_wake({"job_id":job.job_id,"status":job.status.value,"action":job.action})
    if req.revision is None:
        raise HTTPException(status_code=422, detail="revision required")
    return accepted_with_wake(workbench.submit(req.action, req.target, req.revision))


@app.get("/api/actions/{job_id}")
def get_action(job_id: str) -> dict[str, Any]:
    validate_id(job_id)
    for status in ("pending", "processing", "done", "failed"):
        path = jobs.root / f"{job_id}.{status}.json"
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
        require_table(conn, "upload_history")
        where = "WHERE stage != 'staged'" if stage is None else "WHERE stage = ?"
        params: list[Any] = [] if stage is None else [stage]
        total = conn.execute(f"SELECT COUNT(*) FROM upload_queue {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM upload_queue {where} ORDER BY id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            path = Path(item['video_path']).resolve()
            if path.is_relative_to(workbench.artifacts):
                segment_path = workbench.root / f'{path.parent.name}.json'
                if segment_path.is_file():
                    segment = workbench.load(path.parent.name)
                    if str(path) in [segment.get('final_path'), *segment.get('artifact_history', [])]:
                        item.update(segment_id=segment['segment_id'],
                                    source_task_id=task_id_from_source_relpath(segment['source_relpath']))
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        conn.close()


@app.post("/api/uploads/retry")
def retry_upload(req: UploadRetryRequest) -> dict[str, Any]:
    row = upload_queue.get_by_path(req.video_path)
    if row is None:
        raise HTTPException(status_code=404,detail="upload row missing")
    if not manual_retry_allowed(row["stage"], row["remote_filename"]):
        raise HTTPException(
            status_code=422,
            detail="manual retry only for failed rows without remote_filename",
        )
    job = jobs.submit("retry_upload", req.video_path, {})
    return accepted_with_wake({"job_id": job.job_id, "status": job.status.value})


@app.post('/api/uploads/cancel')
def cancel_upload(req: UploadRetryRequest) -> dict[str, Any]:
    try:
        return upload_queue.cancel(req.video_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/worker/wake")
def wake_worker():
    (worker_control.runtime / 'worker-paused').unlink(missing_ok=True)
    return worker_control.wake()


@app.post("/api/worker/stop")
def stop_worker():
    return worker_control.stop()


@app.post('/api/uploader/{action}')
def control_uploader(action: str):
    return worker_control.uploader(action)


@app.get('/api/config/summary')
def config_summary():
    import importlib.util
    import shutil
    from dataclasses import asdict
    data = asdict(settings)
    cookie = settings.paths.cookie_file or Path('.secrets/bilibili.cookie')
    from bilive.worker.preflight import asr_dependency_snapshot
    from bilive.config.web_settings import editable_values, schema
    saved = load_settings(settings.paths.config_path) if settings.paths.config_path else settings
    return {'settings':data, 'editor': {'values': editable_values(saved), 'schema': schema(),
            'runtime_values': editable_values(settings)}, 'asr':asr_dependency_snapshot(settings), 'diagnostics': {
        'ffmpeg':bool(shutil.which('ffmpeg')), 'ffprobe':bool(shutil.which('ffprobe')),
        'mimo_key_configured':bool(os.environ.get('MIMO_API_KEY')),
        'cookie_configured':cookie.is_file(), 'login_verified':False,
        'openai_installed':importlib.util.find_spec('openai') is not None,
        'faster_whisper_installed':importlib.util.find_spec('faster_whisper') is not None,
        'bilitool_installed':importlib.util.find_spec('bilitool') is not None,
        'videos_available':settings.videos_root().is_dir(),
        'upload_database_available':(settings.paths.db_path or Path('src/db/data.db')).is_file(),
    }}


class DefaultSubtitleStyleRequest(BaseModel):
    style: dict[str, Any]


class AnalysisAutomationRequest(BaseModel):
    auto_start: StrictBool


class SettingsChangesRequest(BaseModel):
    changes: dict[str, dict[str, Any]]
    style: dict[str, Any] | None = None


def cancel_automatic_pending():
    cancelled = 0
    for _, job in jobs.valid_jobs(ActionJobStatus.PENDING):
        if job.action == 'start_analysis' and job.payload.get('automatic'):
            try:
                jobs.cancel_pending(job.job_id)
                rel = source_relpath_from_task_id(job.target)
                source = (workbench.videos / rel).resolve()
                if not source.is_relative_to(workbench.videos):
                    raise ValueError('automatic analysis source escapes root')
                source.with_suffix('.mp4.pending').unlink(missing_ok=True)
                cancelled += 1
            except ActionConflict:
                pass
    return cancelled


@app.post('/api/config/settings')
def save_workbench_settings(request: SettingsChangesRequest):
    from bilive.config.web_settings import save_changes, validate_changes
    from bilive.vendor.subtitle_style import validate_style, SubtitleStyle
    if settings.paths.config_path is None:
        raise HTTPException(status_code=503, detail='当前服务没有可保存的配置文件')
    changes = dict(request.changes)
    # Appearance uses the existing validator but joins the same atomic TOML write.
    if request.style is not None:
        validate_style(request.style)
        style = SubtitleStyle.from_mapping(request.style).to_mapping()
        changes['edit'] = dict.fromkeys(('alignment', 'outline', 'primary_colour', 'outline_colour'))
        changes['edit'].update({'font_family': style.pop('font_name'), **style})
    if not request.changes and request.style is None:
        raise ValueError('没有设置修改')
    if request.changes:
        validate_changes(request.changes)
    with workbench.lock():
        result = save_changes(settings.paths.config_path, changes)
        saved = load_settings(settings.paths.config_path)
        settings.analysis.auto_start = saved.analysis.auto_start
        settings.edit = saved.edit
        settings.publication = saved.publication
        settings.retention = saved.retention
        workbench.default_style = settings.subtitle_style()
        result['cancelled_pending'] = cancel_automatic_pending() if not saved.analysis.auto_start else 0
    result['style'] = saved.subtitle_style()
    return result


@app.post('/api/config/analysis-automation')
def save_analysis_automation(request: AnalysisAutomationRequest):
    from bilive.config.settings import save_analysis_auto_start
    if settings.paths.config_path is None:
        raise HTTPException(status_code=503, detail='当前服务没有可保存的配置文件')
    cancelled = 0
    with workbench.lock():
        enabled = save_analysis_auto_start(settings.paths.config_path, request.auto_start)
        settings.analysis.auto_start = enabled
        if not enabled:
            for _, job in jobs.valid_jobs(ActionJobStatus.PENDING):
                if job.action == 'start_analysis' and job.payload.get('automatic'):
                    try:
                        jobs.cancel_pending(job.job_id)
                        source = workbench.source(source_relpath_from_task_id(job.target))
                        source.with_suffix('.mp4.pending').unlink(missing_ok=True)
                        cancelled += 1
                    except ActionConflict:
                        pass  # Already claimed; execution rechecks the setting.
    return {'auto_start':enabled, 'cancelled_pending':cancelled}


@app.post('/api/config/subtitle-style')
def save_default_subtitle_style(request: DefaultSubtitleStyleRequest):
    from bilive.config.settings import save_subtitle_style
    if settings.paths.config_path is None:
        raise HTTPException(status_code=503, detail='当前服务没有可保存的配置文件')
    with workbench.lock():
        style = save_subtitle_style(settings.paths.config_path, request.style)
        settings.edit = load_settings(settings.paths.config_path).edit
        workbench.default_style = dict(style)
    return {'style': style, 'scope': 'new_segments'}


class StageUploadRequest(BaseModel):
    video_path: str
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class ActivateRequest(BaseModel):
    video_path: str


class WithdrawRequest(BaseModel):
    video_path: str


@app.post("/api/uploads/stage")
def stage_upload(req: StageUploadRequest) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="use current segment actions through Worker")


@app.post("/api/uploads/activate")
def activate_upload(req: ActivateRequest) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="use current segment actions through Worker")


@app.post("/api/uploads/withdraw")
def withdraw_upload(req: WithdrawRequest) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="use current segment actions through Worker")


@app.get("/api/services")
def service_status() -> dict[str, Any]:
    worker_status = worker_control.status()
    try:
        recorder_client.rooms(1, 10)
        recorder = {'state':'ok','detail':'blrec native API reachable'}
    except RecorderUnavailable as exc:
        recorder = {'state':'unavailable','detail':str(exc)}
    return {
        "recorder": recorder,
        "workbench": {"state": "ok", "detail": "interface service"},
        "worker": {**worker_status, "busy": jobs.busy()},
        "uploader": worker_status.get("uploader", {"state":"unknown","detail":"Worker offline"}),
    }


@app.get('/api/streamers/{room}/profile')
def streamer_profile(room: str):
    from bilive.vendor import streamer_profiles as profiles
    from bilive.lifecycle.profiles import room_from_source
    paths = [s['final_path'] for p in workbench.root.glob('*.json')
             if room_from_source((s := workbench.load(p.stem))['source_relpath']) == room and s.get('final_path')]
    return {'profile':profiles.read_streamer_profile(workbench.videos, room),
            'recent_published':upload_queue.recent_published(paths),
            'evidence':profiles.streamer_evidence_summary(workbench.videos, room),
            'recommendations':profiles.read_streamer_recommendations(workbench.videos, room)}


@app.post('/api/streamers/{room}/profile')
def save_streamer_profile(room: str, payload: dict[str, Any]):
    from bilive.vendor import streamer_profiles as profiles
    profiles.patch_streamer_profile(workbench.videos, room, payload)
    return streamer_profile(room)


@app.get('/api/streamers/{room}/experiences')
def streamer_experiences(room: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    from bilive.vendor import streamer_profiles as profiles
    profiles._validate_room_id(room)
    rows = profiles.read_experiences(workbench.videos, room_id=room)
    return {'items':list(reversed(rows))[offset:offset+limit], 'total':len(rows), 'limit':limit,'offset':offset}


@app.post('/api/streamers/{room}/recommendations/generate')
def generate_recommendation(room: str):
    from bilive.vendor import streamer_profiles as profiles
    profiles.generate_streamer_recommendation(workbench.videos, room)
    return streamer_profile(room)


@app.post('/api/streamers/{room}/recommendations/{identifier}/apply')
def apply_recommendation(room: str, identifier: str):
    from bilive.vendor import streamer_profiles as profiles
    return profiles.apply_streamer_recommendation(workbench.videos, room, identifier)


@app.get('/api/eagle/source-recordings')
def eagle_source_recordings():
    from bilive.lifecycle.eagle import source_index
    return JSONResponse(source_index(workbench), headers={'Access-Control-Allow-Origin':'*'})


@app.get('/api/recordings/{task_id}')
def source_recording(task_id: str):
    validate_id(task_id)
    rel = source_relpath_from_task_id(task_id)
    source = workbench.source(rel)
    from bilive.recorder.fragments import is_recording_fragment
    files = [p for p in sorted(workbench.videos.rglob('*.mp4')) if not is_recording_fragment(p)]
    if source not in files:
        raise HTTPException(status_code=404, detail='recording fragment excluded')
    index = files.index(source)
    return {'task_id':task_id,'source_relpath':rel,'page_offset':(index // 20) * 20}


# 工作台静态页（设计稿六页面）
_FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIR), name="frontend-assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_FRONTEND_DIR / "index.html")
