from pathlib import Path

from bilive.domain.models import (
    SubtitleLine,
    clamp_subtitle_times,
    segment_id_from_source_and_range,
    source_relpath_from_task_id,
    task_id_from_source_relpath,
)
from bilive.domain.states import (
    decision_continues_to_auto_pipeline,
    manual_retry_allowed,
    trash_status_after_move,
)


def test_task_id_roundtrip():
    rel = "102468-阿橙/2026-09-30T21-10-00.flv"
    tid = task_id_from_source_relpath(rel)
    assert source_relpath_from_task_id(tid) == rel


def test_segment_id_stable_for_same_range():
    a = segment_id_from_source_and_range("room/s.mp4", 1000, 2000)
    b = segment_id_from_source_and_range("room/s.mp4", 1000, 2000)
    c = segment_id_from_source_and_range("room/s.mp4", 1000, 3000)
    assert a == b
    assert a != c


def test_subtitle_rejects_end_before_start():
    try:
        SubtitleLine(1, 2.0, 1.0, "x").validate()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_subtitle_clamp_within_tolerance():
    lines = [SubtitleLine(1, 0.0, 10.01, "hello")]
    out = clamp_subtitle_times(lines, duration=10.0, tolerance=0.01)
    assert out[0].end == 10.0


def test_subtitle_rejects_beyond_tolerance():
    try:
        clamp_subtitle_times([SubtitleLine(1, 0.0, 10.5, "x")], duration=10.0, tolerance=0.01)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_quality_gate_requires_all_three():
    scores = {"quality": 0.9, "completeness": 0.9, "confidence": 0.9}
    assert decision_continues_to_auto_pipeline("keep", scores, is_duplicate=False)
    assert not decision_continues_to_auto_pipeline("keep", {**scores, "confidence": 0.5}, False)
    assert not decision_continues_to_auto_pipeline("keep", scores, is_duplicate=True)
    assert not decision_continues_to_auto_pipeline("review", scores, False)
    assert not decision_continues_to_auto_pipeline("keep", None, False)


def test_manual_retry_only_failed_without_remote():
    assert manual_retry_allowed("failed", None)
    assert not manual_retry_allowed("failed", "remote.flv")
    assert not manual_retry_allowed("queued", None)
    assert not manual_retry_allowed("publishing", None)


def test_partial_trash_is_not_success():
    assert trash_status_after_move([True, True]) == ("done", None)
    status, reason = trash_status_after_move([True, False])
    assert status == "failed" and reason == "partial_move"
    status, reason = trash_status_after_move([False, False])
    assert status == "failed" and reason == "unknown_result_stop"
