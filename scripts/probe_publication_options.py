"""Probe lookup APIs and an empty collection request; never submit a video."""
from datetime import datetime, timezone
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bilive.config.settings import load_settings
from bilive.vendor.upload.bilibili_runtime import BilibiliRuntimeClient
from bilive.vendor.upload.cookie import parse_cookie_file

def main():
    settings = load_settings(ROOT / 'bilive-server.toml')
    client = BilibiliRuntimeClient(cookies=parse_cookie_file(settings.paths.cookie_file or ROOT / '.secrets/bilibili.cookie'))
    try:
        if not client.check_login():
            raise ValueError('Login unavailable')
        options = client.publication_options
        topics = options.search_topics('搞笑研究所')
        seasons = options.list_seasons()
        info = options.topic_info(topics['items'][0]) if topics['items'] else None
        if seasons['items']:
            options.validate_options(season=seasons['items'][0])
        archives = options.request('/x/web/archives', params={'status': 'pubed', 'pn': 1, 'ps': 1})
        aid = archives['arc_audits'][0]['Archive']['aid']
        view = options.request('/x/vupre/web/archive/view', params={'aid': aid})
        current = options.request('/x2/creative/web/season/aid', params={'id': aid}, allow_missing=True)
        snapshot = {'verified_at': datetime.now(timezone.utc).isoformat(), 'login': True,
                    'topics': topics, 'topic_info': info, 'seasons': seasons,
                    'archive_view': {'code': 0, 'aid_matches': view['archive']['aid'] == aid,
                                     'cid_available': bool(view.get('videos', [{}])[0].get('cid'))},
                    'season_lookup': {'code': 0 if current else -404, 'id': current.get('id')},
                    'write_verification': 'No real submission or collection association performed'}
        if '--probe-write-route' in sys.argv:
            response = client.session.post('https://member.bilibili.com/x2/creative/web/season/section/episodes/add',
                params={'csrf': client.cookies['bili_jct']}, json={'sectionId': 0, 'episodes': []}, timeout=20)
            payload = response.json()
            snapshot['empty_write_probe'] = {'http': response.status_code, 'code': payload.get('code'),
                                            'body': {'sectionId': 0, 'episodes': []}}
        output = ROOT / 'artifacts/research/publication-options-20261011/probe.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        print('LOOKUP_APIS_PASS', 'topics', len(topics['items']), 'seasons', len(seasons['items']))
        print('write_probe', snapshot.get('empty_write_probe'), 'evidence', str(output))
    except Exception as exc:
        print('Probe failed:', type(exc).__name__)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
