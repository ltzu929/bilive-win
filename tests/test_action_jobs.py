from pathlib import Path

from bilive.jobs.action_jobs import ActionConflict, ActionJobStore


def test_identical_finalize_reuses_job(tmp_path: Path):
    store = ActionJobStore(tmp_path / "jobs")
    j1 = store.submit("finalize", "seg-1", {"revision": 3})
    j2 = store.submit("finalize", "seg-1", {"revision": 3})
    assert j1.job_id == j2.job_id


def test_different_payload_conflicts(tmp_path: Path):
    store = ActionJobStore(tmp_path / "jobs")
    store.submit("finalize", "seg-1", {"revision": 3})
    try:
        store.submit("finalize", "seg-1", {"revision": 4})
    except ActionConflict:
        return
    raise AssertionError("expected ActionConflict")


def test_two_claimers_cannot_claim_same_job(tmp_path: Path):
    store = ActionJobStore(tmp_path / "jobs")
    store.submit("render", "seg-2", {})
    a = store.claim("worker-a")
    b = store.claim("worker-b")
    assert a is not None
    assert b is None


def test_complete_writes_done_and_removes_processing(tmp_path: Path):
    store = ActionJobStore(tmp_path / "jobs")
    job = store.submit("reburn", "seg-3", {})
    claimed = store.claim("w1")
    assert claimed is not None
    store.complete(claimed, {"ok": True})
    assert (tmp_path / "jobs" / f"{job.job_id}.done.json").is_file()
    assert not (tmp_path / "jobs" / f"{job.job_id}.processing.json").is_file()
