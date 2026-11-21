"""Preview/backfill saved human feedback without analysis, uploads or media lifecycle changes."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from bilive.lifecycle.profiles import sync_review_feedback
from bilive.media.subtitle_store import SubtitleStore
from bilive.vendor import streamer_profiles
from bilive.worker.locks import ProcessLock


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--videos', type=Path, default=Path('Videos'))
    parser.add_argument('--artifacts', type=Path, default=Path('artifacts'))
    parser.add_argument('--room')
    parser.add_argument('--classifications', type=Path, help='Explicit reviewed segment-id → {kind, basis} JSON; never infer categories from keywords')
    parser.add_argument('--write', action='store_true', help='Append evidence and prepare pending recommendations; never apply guidance')
    args = parser.parse_args(argv)
    artifacts = args.artifacts.resolve()
    root = artifacts / 'segments'
    # Evidence collection requires neither an upload connection nor a runnable source.
    wb = SimpleNamespace(videos=args.videos.resolve(), root=root,
                         subtitles=SubtitleStore(artifacts / 'subtitles'),
                         load=lambda sid: json.loads((root / f'{sid}.json').read_text(encoding='utf-8')))
    classifications = json.loads(args.classifications.read_text(encoding='utf-8')) if args.classifications else None
    if args.write:
        with ProcessLock(artifacts / 'workbench.lock'):
            result = sync_review_feedback(wb, room=args.room, classifications=classifications, write=True)
            for room in sorted({r['room_id'] for r in result['records'] if r}):
                streamer_profiles.generate_streamer_recommendation(wb.videos, room)
    else:
        result = sync_review_feedback(wb, room=args.room, classifications=classifications)
    result['experience_ids'] = [r['experience_id'] for r in result.pop('records') if r]
    result['write'] = args.write
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    main()
