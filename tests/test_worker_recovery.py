import json
import time
from pathlib import Path

from bilive.jobs.action_jobs import ActionJobStore
from bilive.worker.locks import ProcessLock
from bilive.worker.runner import Worker


def test_empty_worker_exits_after_idle_without_first_action(tmp_path):
    worker = Worker(ActionJobStore(tmp_path / 'jobs'), None, tmp_path / 'worker.lock')
    assert worker.run_forever(idle_timeout=0.01, idle_check_interval=0.01) == 0


def test_live_owner_never_reclaimed_even_if_old(tmp_path):
    jobs = ActionJobStore(tmp_path / "jobs")
    jobs.submit("retry_upload", "a.mp4", {})
    with ProcessLock(jobs.root / "owners" / "live-owner.lock"):
        job = jobs.claim("live-owner")
        assert jobs.owner_alive("live-owner")
        assert jobs.requeue_orphans(time.time() + 99999, stale_seconds=0) == 0
        assert (jobs.root / f"{job.job_id}.processing.json").exists()
    assert not jobs.owner_alive("live-owner")
    assert jobs.requeue_orphans(time.time() + 99999, stale_seconds=0) == 1
    assert jobs.claim("next").job_id == job.job_id


def test_bad_job_preserved_and_valid_job_continues(tmp_path):
    jobs = ActionJobStore(tmp_path / "jobs")
    broken = jobs.root / "broken.pending.json"
    broken.write_text("{bad JSON", encoding="utf-8")
    good = jobs.submit("render", "segment", {})
    assert jobs.claim("worker").job_id == good.job_id
    quarantined = list((jobs.root / "quarantine").glob("*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == "{bad JSON"


def test_dependencies_block_before_claim_but_other_jobs_run(tmp_path):
    jobs = ActionJobStore(tmp_path / "jobs")
    blocked = jobs.submit("finalize", "first", {})
    ready = jobs.submit("retry_upload", "second", {})
    check = lambda j: (False, "FFmpeg unavailable") if j.action == "finalize" else (True, "")
    assert jobs.claim("owner", check).job_id == ready.job_id
    pending = jobs.load_job(jobs.root / f"{blocked.job_id}.pending.json")
    assert pending.progress == {"phase":"blocked", "reason":"FFmpeg unavailable"}
    assert pending.owner is None


def test_terminal_result_wins_over_processing_leftover(tmp_path):
    jobs = ActionJobStore(tmp_path / "jobs")
    pending = jobs.submit("render", "one", {})
    job = jobs.claim("old-owner")
    processing = jobs.root / f"{job.job_id}.processing.json"
    saved = processing.read_text()
    jobs.complete(job, {"ok":True})
    processing.write_text(saved)
    assert jobs.requeue_orphans(time.time() + 99999, stale_seconds=0) == 0
    assert not processing.exists()
    assert jobs.claim("new-owner") is None


def test_worker_does_not_fail_job_when_preflight_is_unavailable(tmp_path):
    jobs = ActionJobStore(tmp_path / "jobs")
    job = jobs.submit("finalize", "segment", {})
    worker = Worker(jobs, None, tmp_path / "worker.lock", preflight=lambda j:(False,"model unavailable"))
    assert worker.run_once() is False
    assert jobs.busy()
    assert (jobs.root / f"{job.job_id}.pending.json").exists()
    assert not list(jobs.root.glob("*.failed.json"))


def test_owner_probe_is_read_only_when_missing(tmp_path):
    jobs = ActionJobStore(tmp_path / "jobs")
    assert jobs.owner_alive("not-running") is False
    assert not (jobs.root / "owners").exists()


def test_preflight_does_not_hold_queue_lock_and_cancel_is_respected(tmp_path):
    jobs=ActionJobStore(tmp_path/'jobs');job=jobs.submit('start_analysis','source',{})
    def probe(current):
        jobs.submit('render','another',{})
        jobs.cancel_pending(current.job_id)
        return True,''
    assert jobs.claim('worker',probe) is None
    assert (jobs.root/f'{job.job_id}.done.json').is_file()
    assert not (jobs.root/f'{job.job_id}.processing.json').exists()


def _competing_claim(root, barrier, output):
    jobs=ActionJobStore(Path(root))
    def probe(job):
        barrier.wait(timeout=15)
        return True,''
    claimed=jobs.claim('worker',probe)
    output.put(claimed.job_id if claimed else None)


def test_two_processes_claim_exactly_once_after_simultaneous_preflight(tmp_path):
    import multiprocessing
    jobs=ActionJobStore(tmp_path/'jobs');job=jobs.submit('render','segment',{})
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);output=ctx.Queue()
    processes=[ctx.Process(target=_competing_claim,args=(str(jobs.root),barrier,output)) for _ in range(2)]
    for process in processes:process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    claims=[output.get(timeout=2),output.get(timeout=2)]
    assert claims.count(job.job_id) == 1 and claims.count(None) == 1


def test_interrupted_claim_pending_leftover_cannot_keep_terminal_job_busy(tmp_path):
    jobs=ActionJobStore(tmp_path/'jobs');job=jobs.submit('render','segment',{})
    pending=jobs.root/f'{job.job_id}.pending.json';saved=pending.read_text()
    claimed=jobs.claim('worker')
    pending.write_text(saved)  # Simulated crash boundary before pending unlink.
    jobs.complete(claimed,{'ok':True})
    jobs.requeue_orphans(time.time(),stale_seconds=0)
    assert not pending.exists() and not jobs.busy() and jobs.claim('other') is None
    assert (jobs.root/f'{job.job_id}.done.json').is_file()
