from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

import pytest

from bilive.lifecycle.retention import DAY, LifecycleStore, RetentionPlanner, apply_trash_results
from bilive.media.remux import RemuxError, remux_recording, verify_mp4


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not installed")


def _make_flv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            "-f", "flv",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_remux_verifies_then_deletes_source(tmp_path: Path):
    flv = tmp_path / "room" / "2026-09-23T21-10-00.flv"
    _make_flv(flv)
    result = remux_recording(flv, delete_source=True)
    assert result.verified is True
    assert result.deleted_flv is True
    mp4 = Path(result.mp4_path)
    assert mp4.is_file() and mp4.stat().st_size > 0
    assert not flv.exists()


def test_remux_keeps_source_when_verify_fails(tmp_path: Path):
    flv = tmp_path / "room" / "bad.flv"
    flv.parent.mkdir(parents=True)
    flv.write_bytes(b"not a real flv")
    with pytest.raises(RemuxError):
        remux_recording(flv, delete_source=True)
    assert flv.exists()


def test_verify_mp4_rejects_empty(tmp_path: Path):
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    with pytest.raises(RemuxError):
        verify_mp4(p, [])


def test_retention_warns_day5_and_expires_day7(tmp_path: Path):
    store = LifecycleStore(tmp_path / "life")
    planner = RetentionPlanner(store)
    rel = "room/2026-09-23T21-10-00.flv"
    # 文件名解析出的 UTC 起点之后
    base = datetime.datetime(2026, 9, 23, 21, 10, 0, tzinfo=datetime.timezone.utc).timestamp()
    now = base + 60
    plan0 = planner.evaluate(rel, now=now)
    assert plan0["action"] == "keep"

    plan5 = planner.evaluate(rel, now=base + 5 * DAY + 120)
    assert plan5["warn_day5"] is True
    assert plan5["expired_day7"] is False

    plan7 = planner.evaluate(rel, now=now + 7 * DAY + 10)
    assert plan7["expired_day7"] is True
    assert plan7["action"] == "force_expired"


def test_retention_does_not_postpone_existing_deadline(tmp_path: Path):
    store = LifecycleStore(tmp_path / "life")
    planner = RetentionPlanner(store)
    rel = "room/2026-09-23T21-10-00.flv"
    base = datetime.datetime(2026, 9, 23, 21, 10, 0, tzinfo=datetime.timezone.utc).timestamp()
    now = base + 60
    first = planner.evaluate(rel, now=now)
    second = planner.evaluate(rel, now=now + DAY * 2)
    assert first["deadline"] == second["deadline"]


def test_force_expired_trash_protects_and_records_partial(tmp_path: Path):
    store = LifecycleStore(tmp_path / "life")
    planner = RetentionPlanner(store)
    rel = "room/2026-09-23T21-10-00.flv"
    base = datetime.datetime(2026, 9, 23, 21, 10, 0, tzinfo=datetime.timezone.utc).timestamp()
    now = base + 8 * DAY
    plan = planner.plan_trash(rel, protected=["final_output.mp4", "review.json"], now=now)
    assert plan["queued"] is True
    assert plan["action"] == "force_expired"
    names = [Path(p).name for p in plan["frozen_manifest"]]
    assert "final_output.mp4" not in names
    assert "review.json" not in names

    ok = apply_trash_results(
        store,
        rel,
        [
            {"path": "a.mp4", "ok": True},
            {"path": "b.xml", "ok": False, "status": "unknown"},
        ],
    )
    assert ok["trash_status"] == "failed"
    assert ok["reason"] == "unknown_result_stop"
