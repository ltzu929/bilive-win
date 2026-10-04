"""Explicit historical recovery/audit, separate from daily retention jobs."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from bilive.config.settings import load_settings
from bilive.config.secrets import load_secret_env
from bilive.db.conn import open_connection, require_table
from bilive.vendor.recording_recovery import recover_recording, validate_media
from bilive.worker.locks import ProcessLock


def inspect_history(videos: Path, artifacts: Path, db: Path, *, execute=False, delete_invalid=False, audit_mp4=False):
    root=videos.resolve()
    if not root.is_dir():raise FileNotFoundError('historical videos root unavailable')
    protected=set()
    for path in (artifacts/'segments').glob('*.json'):
        s=json.loads(path.read_text(encoding='utf-8'))
        protected.update(Path(v).resolve() for v in [s.get('final_path'),s.get('generated_path'),*s.get('artifact_history',[])] if v)
    # Protection must be based on the authoritative queue, never create/repair it.
    conn=open_connection(db,readonly=True)
    try:
        require_table(conn,'upload_queue');require_table(conn,'upload_history')
        protected.update(Path(row['video_path']).resolve() for row in conn.execute('SELECT video_path FROM upload_queue'))
    finally:conn.close()
    def checked(path):
        resolved=path.resolve()
        if not resolved.is_relative_to(root):raise ValueError('historical path escapes root')
        return resolved not in protected
    items=[]
    for source in sorted(root.rglob('*.flv')):
        if not checked(source) or not checked(source.with_suffix('.mp4')):
            items.append({'source':str(source),'status':'protected'});continue
        if not checked(source.with_name(source.stem+'.partial.mp4')):raise ValueError('protected temporary recovery path')
        items.append(recover_recording(source,execute=execute,delete_invalid=delete_invalid))
    audits=[]
    if audit_mp4:
        for media in sorted(root.rglob('*.mp4')):
            if not checked(media):audits.append({'path':str(media),'status':'protected'});continue
            validation=validate_media(media)
            status='valid' if validation['valid'] else 'invalid_preserved'
            if not validation['valid'] and execute and delete_invalid:
                media.unlink();status='deleted_invalid'
            audits.append({'path':str(media),'status':status,'validation':validation})
    return {'videos_root':str(root),'execute':execute,'delete_invalid':delete_invalid,
            'recordings':items,'mp4_audit':audits}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--videos-root',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--delete-invalid',action='store_true')
    parser.add_argument('--audit-mp4',action='store_true')
    args=parser.parse_args(argv)
    load_secret_env(Path('.secrets/env'));settings=load_settings()
    videos=args.videos_root or settings.videos_root()
    artifacts=Path(os.environ.get('BILIVE_ARTIFACTS_DIR','artifacts'))
    report_path=args.report.resolve()
    db=(settings.paths.db_path or Path('src/db/data.db')).resolve()
    if (report_path.suffix.lower() != '.json' or report_path.is_relative_to(videos.resolve()) or report_path == db
            or (report_path.is_relative_to(artifacts.resolve()) and not report_path.is_relative_to((artifacts/'reports').resolve()))):
        raise ValueError('report must be a JSON file outside media and operational artifacts')
    def run():
        return inspect_history(videos,artifacts,settings.paths.db_path or Path('src/db/data.db'),
            execute=args.execute,delete_invalid=args.delete_invalid,audit_mp4=args.audit_mp4)
    if args.execute:
        # No media operation overlaps an owned Worker or upload consumer.
        from contextlib import ExitStack
        runtime=Path(os.environ.get('BILIVE_RUNTIME_DIR','.runtime'))
        with ExitStack() as stack:
            for lock in (runtime/'worker.instance.lock',runtime/'upload.lock',artifacts/'workbench.lock'):
                stack.enter_context(ProcessLock(lock))
            jobs=Path(os.environ.get('BILIVE_JOBS_DIR','.bilive-jobs'))
            if any(jobs.glob('*.pending.json')) or any(jobs.glob('*.processing.json')):
                raise RuntimeError('historical execution requires an idle durable queue')
            report=run()
    else:report=run()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(args.report.resolve()),'execute':args.execute},ensure_ascii=False))
    return 0


if __name__ == '__main__':raise SystemExit(main())
