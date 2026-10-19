from __future__ import annotations

import json
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
def media_env(tmp_path: Path, monkeypatch):
    # Software path must remain usable on CI hosts without NVIDIA hardware.
    monkeypatch.setenv("BILIVE_VIDEO_ENCODER", "libx264")
    videos = tmp_path / "Videos"
    artifacts = tmp_path / "artifacts"
    videos.mkdir()
    source = videos / "room1" / "session.mp4"
    source.parent.mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            # Red / green / blue two-second blocks expose a wrong input seek.
            "-f", "lavfi", "-i", "color=c=red:s=320x240:r=15:d=6,"
            "drawbox=c=green:t=fill:enable='gte(t,2)',"
            "drawbox=c=blue:t=fill:enable='gte(t,4)'",
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


def _frame(path: str, seconds: float) -> bytes:
    """Decode the delivered picture; ASS text alone cannot prove a burn."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(seconds), "-i", path,
         "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
        capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert len(result.stdout) == 320 * 240 * 3, "missing frame or changed dimensions"
    return result.stdout


def _white_pixels(frame: bytes) -> set[int]:
    # Solid primary-colour source blocks have no white pixels. A tolerance keeps
    # this independent of exact font rasterisation and codec output bytes.
    return {i // 3 for i in range(0, len(frame), 3) if min(frame[i:i + 3]) > 180}


def _save_visible_subtitles(env, segment, text, revision, start=0):
    env["subs"].save(segment, 2.0, [SubtitleLine(1, start, 1, text)], revision)
    # ASS uses a 1920x1080 canvas. An explicit readable style avoids testing
    # subpixel glyphs after scaling to this small, inexpensive video fixture.
    directory = env["artifacts"] / "segments"
    directory.mkdir(exist_ok=True)
    (directory / f"{segment}.json").write_text(json.dumps({
        "subtitle_style": {"font_name": "Arial", "font_size": 96, "margin_v": 60},
    }), encoding="utf-8")


def test_save_subtitles_then_finalize_creates_final(media_env):
    seg = "segA"
    _save_visible_subtitles(media_env, seg, "MMMMMMMMMMMM", revision=2, start=0.2)
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
    info = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(final)],
        timeout=20,
    ))
    assert abs(float(info["format"]["duration"]) - 2.0) < 0.15
    before = _frame(result.final_path, 0)
    during = _frame(result.final_path, 0.5)
    after = _frame(result.final_path, 1.5)
    # Requested source seconds 1..3 straddle the red/green boundary at 2.
    assert during[0] > 200 and during[1] < 30 and during[2] < 30, "wrong clip beginning"
    assert after[1] > 90 and after[0] < 30 and after[2] < 30, "wrong clip ending"
    assert not _white_pixels(before), "subtitles appeared before their cue"
    assert len(_white_pixels(during)) >= 5, "saved subtitles were not burned into the picture"
    assert not _white_pixels(after), "subtitles remained after their cue"


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
    _save_visible_subtitles(media_env, seg, "MMMMMMMMMMMM", revision=1)
    r1 = media_env["fin"].finalize(seg, media_env["rel"], 0.0, 2.0, revision=1)
    original = _white_pixels(_frame(r1.final_path, 0.5))
    _save_visible_subtitles(media_env, seg, "IIII", revision=2)
    r2 = media_env["fin"].reburn(seg, media_env["rel"], 0.0, 2.0, revision=2)
    assert r1.final_path == r2.final_path
    assert Path(r2.final_path).is_file()
    updated = _white_pixels(_frame(r2.final_path, 0.5))
    assert original and updated, "subtitles missing before or after reburn"
    assert len(original ^ updated) >= 5, "reburn still shows the old subtitle picture"


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


@pytest.mark.skipif(not __import__('shutil').which('nvidia-smi'), reason='NVIDIA hardware unavailable')
def test_nvenc_cut_and_subtitle_burn_preserve_duration_and_audio(media_env, monkeypatch):
    monkeypatch.setenv('BILIVE_VIDEO_ENCODER', 'h264_nvenc')
    media_env['subs'].save('gpu', 2.0, [SubtitleLine(1, 0, 1.8, '硬件编码字幕')], revision=1)
    result = media_env['fin'].finalize('gpu', media_env['rel'], 1.0, 3.0, revision=1)
    import json
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
        '-show_format', '-of', 'json', result.final_path]))
    assert abs(float(info['format']['duration']) - 2.0) < 0.15
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert (video['codec_name'], video['pix_fmt']) == ('h264', 'yuv420p')
    assert any(s['codec_type'] == 'audio' for s in info['streams'])
