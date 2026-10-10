"""Read published Bilibili archive statistics; never upload or edit a video."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from bilive.config.settings import load_settings
from bilive.vendor.upload.bilibili_runtime import BilibiliRuntimeClient
from bilive.vendor.upload.cookie import parse_cookie_file


def collect(client, *, days=90, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).timestamp()
    login = client.check_login()
    if not login:
        raise ValueError('B站登录失效，无法读取投稿反馈')
    items = []
    page = 1
    while True:
        response = client.session.get('https://member.bilibili.com/x/web/archives',
            params={'status': 'pubed', 'pn': page, 'ps': 50}, timeout=20)
        response.raise_for_status()
        payload = json.loads(response.content)
        if payload.get('code') != 0:
            raise ValueError(f"B站反馈查询失败，错误码 {payload.get('code')}")
        data = payload.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('page'), dict):
            raise ValueError('B站反馈返回结构变化，未保存不完整快照')
        rows = data.get('arc_audits')
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise ValueError('B站反馈稿件列表格式错误')
        for row in rows:
            archive = row['Archive']
            published = archive.get('ptime') or archive.get('ctime')
            if not isinstance(published, (int, float)) or not archive.get('bvid'):
                raise ValueError('B站稿件缺少发布时间或BV号')
            if published < cutoff:
                continue
            stats = row.get('stat') or {}
            items.append({
                'bvid': archive['bvid'], 'title': archive['title'],
                'published_at': datetime.fromtimestamp(published, timezone.utc).isoformat(),
                'age_days': round((now.timestamp() - published) / 86400, 2),
                'duration_seconds': archive.get('duration'),
                'tags': str(archive.get('tag') or '').split(',') if archive.get('tag') else [],
                'copyright': archive.get('copyright'), 'tid': archive.get('tid'),
                'mission_id': archive.get('mission_id'),
                'source': archive.get('source') or '',
                'statistics': {key: stats.get(key) for key in
                    ('view', 'like', 'coin', 'favorite', 'share', 'reply', 'danmaku')},
            })
        total = data['page'].get('count')
        if not isinstance(total, int) or total < 0:
            raise ValueError('B站反馈缺少稿件总数')
        if page * 50 >= total:
            break
        if len(rows) != 50:
            raise ValueError('B站反馈分页不完整，未保存快照')
        page += 1
        time.sleep(0.5)
    return {
        'collected_at': now.isoformat(), 'window_days': days,
        'account': {'mid': login['mid'], 'uname': login['uname']},
        'source': 'Bilibili creator published-archive API',
        'metrics_note': '累计数据；按发布时间比较。完播率、观看时长、曝光、收入本接口未提供。',
        'items': items,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=90)
    args = parser.parse_args()
    if not 1 <= args.days <= 3650:
        parser.error('--days must be between 1 and 3650')
    settings = load_settings(PROJECT / 'bilive-server.toml')
    cookie = settings.paths.cookie_file or PROJECT / '.secrets/bilibili.cookie'
    client = BilibiliRuntimeClient(cookies=parse_cookie_file(cookie))
    try:
        snapshot = collect(client, days=args.days)
    except Exception as exc:
        # No raw request headers, Cookie values or server error bodies in logs.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f'反馈采集失败：{message}', file=sys.stderr)
        return 1
    finally:
        client.session.close()
    stamp = datetime.fromisoformat(snapshot['collected_at']).strftime('%Y%m%dT%H%M%S%fZ')
    root = PROJECT / 'artifacts/research/publication-feedback'
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{stamp}.json'
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'snapshot': str(path), 'count': len(snapshot['items']),
                      'account': snapshot['account']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
