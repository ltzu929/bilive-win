"""启动 Windows Worker（处理 .bilive-jobs）。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from bilive.config.settings import load_settings
from bilive.db.conn import init_upload_db
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.finalize import Finalizer
from bilive.media.subtitle_store import SubtitleStore
from bilive.upload.queue import UploadQueue
from bilive.worker.runner import Worker
from bilive.media.workbench import Workbench
from bilive.analysis.pipeline import AnalysisPipeline
from bilive.worker.preflight import check_action
from bilive.config.secrets import load_secret_env
from bilive.network import use_direct_network
from bilive.worker.runtime import ConsumerProcess, serve
from bilive.lifecycle.review import SourceReview


def main() -> None:
    parser = argparse.ArgumentParser(description="Bilive Windows Worker")
    parser.add_argument("--videos", type=Path, default=None)
    parser.add_argument("--jobs", type=Path, default=None)
    parser.add_argument("--artifacts", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--max-jobs", type=int, default=None)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=2235)
    args = parser.parse_args()

    load_secret_env(Path(".secrets/env"))
    use_direct_network()
    settings = load_settings()
    videos = args.videos or settings.paths.videos_dir
    videos = Path(videos)
    artifacts = args.artifacts or Path(os.environ.get("BILIVE_ARTIFACTS_DIR", "artifacts"))
    jobs_dir = args.jobs or Path(os.environ.get("BILIVE_JOBS_DIR", ".bilive-jobs"))
    runtime = Path(os.environ.get("BILIVE_RUNTIME_DIR", ".runtime"))
    db_path = args.db or settings.paths.db_path or Path("src/db/data.db")
    # 写侧启动流程：初始化上传库后再创建服务（规格 §8）
    init_upload_db(Path(db_path))
    jobs = ActionJobStore(jobs_dir)
    subs = SubtitleStore(artifacts / "subtitles")
    finalizer = Finalizer(videos, artifacts, subs)
    uploads = UploadQueue(Path(db_path))
    workbench = Workbench(videos, artifacts, jobs, subs, uploads,
        settings.subtitle_style())
    workbench.default_style_provider = lambda: load_settings(settings.paths.config_path).subtitle_style()
    worker = Worker(
        jobs,
        finalizer,
        runtime / "worker.lock",
        upload_queue=uploads,
        workbench=workbench,
        analysis=AnalysisPipeline(settings, workbench, finalizer),
        lifecycle=SourceReview(workbench),
        auto_analysis_enabled=lambda: load_settings(settings.paths.config_path).analysis.auto_start,
        preflight=lambda job: check_action(job, settings),
    )

    print(f"worker start videos={videos} jobs={args.jobs}")
    consumer = ConsumerProcess(settings,Path(db_path),runtime,Path(__file__).resolve().parents[1])
    if args.serve:
        if args.max_jobs is not None:
            parser.error("--max-jobs is for isolated batch execution, not --serve")
        serve(worker,settings,consumer,args.port)
        return
    # Batch verification deliberately does not launch a real consumer.
    if args.max_jobs is None:
        worker.on_tick = consumer.tick
        worker.external_busy = consumer.busy
    processed = worker.run_forever(
        idle_timeout=settings.worker.idle_timeout,
        idle_check_interval=settings.worker.idle_check_interval,
        max_jobs=args.max_jobs,
    )
    consumer.stop()
    print(f"worker exit processed={processed}")


if __name__ == "__main__":
    main()
