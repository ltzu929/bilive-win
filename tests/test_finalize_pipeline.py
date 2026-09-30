from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bilive.db.conn import init_upload_db
from bilive.domain.models import SubtitleLine
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.finalize import Finalizer, PipelineError
from bilive.media.subtitle_store import SubtitleStore
from bilive.upload.queue import UploadQueue
from bilive.worker.runner import Worker


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


@pytest.fixture
def media_env(tmp_path: Path):
    videos = tmp_path / "Videos"
    artifacts = tmp_path / "artifacts"
    videos.mkdir()
    source = videos / "room1" / "session.mp4"
    source.parent.mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    subs = SubtitleStore(artifacts / "subtitles")
    fin = Finalizer(videos, artifacts, subs)
    jobs = ActionJobStore(tmp_path / ".bilive-jobs")
    db = tmp_path / "upload.db"
    init_upload_db(db)
    uq = UploadQueue(db)
    worker = Worker(jobs, fin, tmp_path / "worker.lock", worker_id="test-worker", upload_queue=uq)
    return {
        "videos": videos,
        "artifacts": artifacts,
        "source": source,
        "rel": "room1/session.mp4",
        "subs": subs,
        "fin": fin,
        "jobs": jobs,
        "worker": worker,
        "uploads": uq,
        "tmp": tmp_path,
    }


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not installed")


def test_save_subtitles_then_finalize_creates_final(media_env):
    seg = "segA"
    media_env["subs"].save(
        seg,
        duration=2.0,
        lines=[SubtitleLine(1, 0.2, 1.8, "你好，世界")],
        revision=2,
    )
    result = media_env["fin"].finalize(
        segment_id=seg,
        source_relpath=media_env["rel"],
        source_start=1.0,
        source_end=3.0,
        revision=2,
    )
    final = Path(result.final_path)
    assert final.is_file() and final.stat().st_size > 0
    assert Path(result.raw_path).is_file()


def test_finalize_requires_saved_subtitles(media_env):
    with pytest.raises(PipelineError):
        media_env["fin"].finalize(
            segment_id="segB",
            source_relpath=media_env["rel"],
            source_start=0.0,
            source_end=2.0,
            revision=1,
        )


def test_finalize_rejects_stale_revision(media_env):
    seg = "segC"
    media_env["subs"].save(seg, 2.0, [SubtitleLine(1, 0, 1, "x")], revision=1)
    with pytest.raises(PipelineError):
        media_env["fin"].finalize(
            segment_id=seg,
            source_relpath=media_env["rel"],
            source_start=0.0,
            source_end=2.0,
            revision=99,
        )


def test_reburn_updates_subtitles_same_path(media_env):
    seg = "segD"
    media_env["subs"].save(seg, 2.0, [SubtitleLine(1, 0, 1, "第一版")], revision=1)
    r1 = media_env["fin"].finalize(seg, media_env["rel"], 0.0, 2.0, revision=1)
    media_env["subs"].save(seg, 2.0, [SubtitleLine(1, 0, 1, "第二版字幕")], revision=2)
    r2 = media_env["fin"].reburn(seg, media_env["rel"], 0.0, 2.0, revision=2)
    assert r1.final_path == r2.final_path
    assert Path(r2.final_path).is_file()


def test_worker_executes_finalize_job(media_env):
    seg = "segE"
    media_env["subs"].save(seg, 2.0, [SubtitleLine(1, 0, 1, "worker 测试")], revision=2)
    job = media_env["jobs"].submit(
        "finalize",
        seg,
        {
            "source_relpath": media_env["rel"],
            "source_start": 0.0,
            "source_end": 2.0,
            "revision": 2,
        },
    )
    assert media_env["worker"].run_once() is True
    done = media_env["jobs"].load_job(
        media_env["jobs"].root / f"{job.job_id}.done.json"
    )
    assert done.result and done.result["ok"] is True
    assert Path(done.result["final_path"]).is_file()


def test_worker_records_failure_when_source_missing(media_env):
    job = media_env["jobs"].submit(
        "finalize",
        "segF",
        {
            "source_relpath": "nope/missing.mp4",
            "source_start": 0.0,
            "source_end": 2.0,
            "revision": 1,
        },
    )
    assert media_env["worker"].run_once() is True
    failed = media_env["jobs"].load_job(
        media_env["jobs"].root / f"{job.job_id}.failed.json"
    )
    assert failed.result and failed.result["ok"] is False


def test_approve_publish_activates_upload_queue(media_env):
    seg = "segG"
    media_env["subs"].save(seg, 2.0, [SubtitleLine(1, 0, 1, "发布")], revision=1)
    media_env["fin"].finalize(seg, media_env["rel"], 0.0, 2.0, revision=1)
    job = media_env["jobs"].submit("approve_publish", seg, {"revision": 1})
    assert media_env["worker"].run_once() is True
    done = media_env["jobs"].load_job(media_env["jobs"].root / f"{job.job_id}.done.json")
    assert done.result["ok"] is True
    assert done.result["upload_stage"] == "queued"
    row = media_env["uploads"].get_by_path(done.result["video_path"])
    assert row and row["stage"] == "queued"
