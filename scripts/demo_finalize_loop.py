"""隔离环境演示：字幕保存 → finalize 任务 → Worker 产出成片。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilive.domain.models import SubtitleLine
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.finalize import Finalizer
from bilive.media.subtitle_store import SubtitleStore
from bilive.worker.runner import Worker


def main() -> int:
    demo = ROOT / ".runtime" / "demo"
    videos = demo / "Videos"
    artifacts = demo / "artifacts"
    videos.mkdir(parents=True, exist_ok=True)
    source = videos / "阿橙" / "夜间杂谈.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=8:size=640x360:rate=24",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                str(source),
            ],
            check=True,
            capture_output=True,
        )
        print("generated demo source:", source)

    subs = SubtitleStore(artifacts / "subtitles")
    fin = Finalizer(videos, artifacts, subs)
    jobs = ActionJobStore(demo / ".bilive-jobs")
    worker = Worker(jobs, fin, demo / "worker.lock", worker_id="demo-worker")

    seg = "demo-seg-第一次上台"
    saved = subs.save(
        seg,
        duration=3.0,
        lines=[
            SubtitleLine(1, 0.1, 1.2, "那是我第一次站上舞台。"),
            SubtitleLine(2, 1.3, 2.8, "其实上台之前特别紧张。"),
        ],
        revision=1,
    )
    print("saved subtitles revision:", saved["revision"])

    job = jobs.submit(
        "reburn",
        seg,
        {
            "source_relpath": "阿橙/夜间杂谈.mp4",
            "source_start": 1.0,
            "source_end": 4.0,
            "revision": saved["revision"],
        },
    )
    print("submitted job:", job.job_id, job.action)

    assert worker.run_once() is True
    done = jobs.load_job(jobs.root / f"{job.job_id}.done.json")
    print("job result:", json.dumps(done.result, ensure_ascii=False, indent=2))
    final = Path(done.result["final_path"])
    print("final exists:", final.is_file(), "size:", final.stat().st_size)
    return 0 if final.is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())
