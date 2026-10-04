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
    print(json.dumps(SourceReview(wb).maintenance(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
