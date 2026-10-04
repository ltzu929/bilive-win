"""Independent uploader; only explicitly activated SQLite rows are eligible."""
import importlib.util
import os
from pathlib import Path

from bilive.config.settings import load_settings
from bilive.config.secrets import load_secret_env
from bilive.network import use_direct_network
from bilive.upload.consumer import UploadConsumer
from bilive.upload.queue import UploadQueue


def main():
    load_secret_env(Path(".secrets/env"))
    use_direct_network()
    settings = load_settings()
    cookie = settings.paths.cookie_file or Path(".secrets/bilibili.cookie")
    if not cookie.is_file():
        raise SystemExit("upload consumer unavailable: ignored Cookie file missing")
    if importlib.util.find_spec("bilitool") is None:
        raise SystemExit("upload consumer unavailable: pinned bilitool dependency missing")
    from bilive.vendor.upload.cookie import parse_cookie_file
    from bilive.vendor.upload.bilibili_runtime import BilibiliRuntimeClient
    client = BilibiliRuntimeClient(cookies=parse_cookie_file(cookie), upload_line=settings.upload.upload_line)
    runtime = Path(os.environ.get("BILIVE_RUNTIME_DIR", ".runtime"))
    consumer = UploadConsumer(UploadQueue(settings.paths.db_path or Path("src/db/data.db")),
                              settings.upload,client,runtime / "upload.lock",runtime / "upload-status.json",
                              Path(os.environ.get('BILIVE_ARTIFACTS_DIR', 'artifacts')) / 'segments')
    consumer.run()


if __name__ == "__main__":
    main()
