"""Daily source retention: persist warnings and enqueue expiry; never drain jobs."""
from pathlib import Path
import os
import json
from bilive.config.settings import load_settings
from bilive.config.secrets import load_secret_env
from bilive.jobs.action_jobs import ActionJobStore
from bilive.media.workbench import Workbench
from bilive.media.subtitle_store import SubtitleStore
from bilive.upload.queue import UploadQueue
from bilive.lifecycle.review import SourceReview


def main():
    load_secret_env(Path('.secrets/env'))
    settings = load_settings()
    artifacts = Path(os.environ.get('BILIVE_ARTIFACTS_DIR', 'artifacts'))
    wb = Workbench(settings.videos_root(), artifacts,
                   ActionJobStore(Path(os.environ.get('BILIVE_JOBS_DIR', '.bilive-jobs'))),
                   SubtitleStore(artifacts / 'subtitles'),
                   UploadQueue(settings.paths.db_path or Path('src/db/data.db')))
    from bilive.lifecycle.runtime import configure_workbench
    configure_workbench(wb, settings.paths.config_path)
    results = SourceReview(wb).maintenance()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if any(row.get('queued') for row in results):
        from bilive.worker.control import WorkerControl
        WorkerControl(Path(__file__).resolve().parents[1],
            Path(os.environ.get('BILIVE_RUNTIME_DIR', '.runtime')),
            int(os.environ.get('BILIVE_WORKER_PORT', '2235'))).wake()


if __name__ == '__main__':
    main()
