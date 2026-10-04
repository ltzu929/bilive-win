"""Separate Python 3.10 blrec entry; native APIs and recorder remain upstream."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
from bilive.config.secrets import load_secret_env
from bilive.network import use_direct_network


def configure(root: Path, videos: Path | None = None, config: Path | None = None):
    load_secret_env(root / '.secrets/env')
    use_direct_network()
    key = os.environ.get('BLREC_API_KEY') or os.environ.get('RECORD_KEY')
    if not key:
        raise ValueError('BLREC_API_KEY is required in .secrets/env or process environment')
    settings = (config or root / 'settings.toml').resolve()
    if not settings.is_file():
        raise FileNotFoundError('settings.toml missing; create from settings.example.toml')
    import toml
    data = toml.loads(settings.read_text(encoding='utf-8'))
    post = data.get('postprocessing', {})
    if (post.get('delete_source') != 'never' or post.get('remux_to_mp4') is not False
            or post.get('inject_extra_metadata') is not False):
        raise ValueError('recorder requires remux/inject=false and delete_source=never')
    if data.get('space', {}).get('recycle_records') is not False:
        raise ValueError('native source recycling must be disabled; Worker owns recycle manifests')
    if data.get('header', {}).get('cookie'):
        raise ValueError('move recorder Cookie from settings.toml to ignored .secrets/bilibili.cookie')
    output = (videos or Path(os.environ.get('BILIVE_VIDEOS_DIR', root / 'Videos'))).resolve()
    logs = Path(os.environ.get('BILIVE_LOG_DIR', root / 'logs')).resolve() / 'record'
    output.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    os.environ.update(BLREC_API_KEY=key, BLREC_CONFIG=str(settings), BLREC_OUT_DIR=str(output),
                      BLREC_LOG_DIR=str(logs), BLREC_PROGRESS='')
    return output


def create_app(root: Path, videos: Path):
    # Native blrec creates its aiohttp connector while importing Application.
    # Construct it inside the serving event loop, as uvicorn's import entry did.
    from bilive.vendor.blrec_patch import main as install_fps_guard
    from bilive.vendor.blrec_danmu_fix import install_danmu_api_fix
    from bilive.recorder.cookie import read_secret_cookie, install_secret_cookie
    from bilive.recorder.events import install_recording_events
    from bilive.jobs.action_jobs import ActionJobStore
    from bilive.worker.control import WorkerControl
    install_fps_guard([])
    install_secret_cookie(read_secret_cookie(root / '.secrets/bilibili.cookie'))
    install_danmu_api_fix()
    from blrec.web import app
    from blrec.web.main import app as native_application
    from bilive.recorder.monitor import install_monitor_controls
    install_monitor_controls(app, native_application)
    jobs = ActionJobStore(Path(os.environ.get('BILIVE_JOBS_DIR', root / '.bilive-jobs')))
    control = WorkerControl(root, Path(os.environ.get('BILIVE_RUNTIME_DIR', root / '.runtime')),
                            int(os.environ.get('BILIVE_WORKER_PORT', '2235')),
                            python=root / '.venv-win/Scripts/python.exe')
    install_recording_events(app, videos, jobs, control)
    from bilive.recorder.gateway import RecorderGateway
    return RecorderGateway(app)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=2233)
    parser.add_argument('--videos-dir', type=Path)
    parser.add_argument('--settings', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    videos = configure(root, args.videos_dir, args.settings)
    import asyncio
    import uvicorn
    async def run():
        app = create_app(root, videos)
        await uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port,
                                            access_log=False)).serve()
    asyncio.run(run())


if __name__ == '__main__':
    main()
