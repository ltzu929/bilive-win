"""启动 Windows Worker（处理 .bilive-jobs）。"""

from __future__ import annotations

import argparse
from pathlib import Path

from bilive.config.settings import load_settings
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.finalize import Finalizer
from bilive.media.subtitle_store import SubtitleStore
from bilive.worker.runner import Worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Bilive Windows Worker")
    parser.add_argument("--videos", type=Path, default=None)
    parser.add_argument("--jobs", type=Path, default=Path(".bilive-jobs"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    parser.add_argument("--max-jobs", type=int, default=None)
    args = parser.parse_args()

    settings = load_settings()
    videos = args.videos or settings.paths.videos_dir
    videos = Path(videos)
    artifacts = Path(args.artifacts)
    jobs = ActionJobStore(Path(args.jobs))
    subs = SubtitleStore(artifacts / "subtitles")
    finalizer = Finalizer(videos, artifacts, subs)
    worker = Worker(jobs, finalizer, Path(".runtime") / "worker.lock")

    print(f"worker start videos={videos} jobs={args.jobs}")
    processed = worker.run_forever(
        idle_timeout=settings.worker.idle_timeout,
        idle_check_interval=settings.worker.idle_check_interval,
        max_jobs=args.max_jobs,
    )
    print(f"worker exit processed={processed}")


if __name__ == "__main__":
    main()
