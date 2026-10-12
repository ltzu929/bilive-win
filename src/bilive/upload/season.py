"""Collection association is separate from a confirmed video submission."""
import json
from bilive.vendor.upload.publication_options import PublicationOptionsError
from bilive.worker.locks import ProcessLock


def join_published_season(queue, client, path):
    row = queue.get_by_path(path)
    if not row or row['stage'] != 'published' or not row.get('season') or not row.get('bvid'):
        raise ValueError('仅已投稿且选择了合集的稿件可以重试加入合集')
    # The consumer keeps its global upload lock while idle. Use a separate
    # per-row lock so manual collection recovery can run with uploader online.
    lock = queue.db_path.parent / '.season-locks' / f'{queue.db_path.name}.{row["id"]}.lock'
    with ProcessLock(lock):
        row = queue.get_by_path(path)
        if row.get('season_status') == 'joined':
            return row
        try:
            client.publication_options.add_to_season(row.get('aid'), row['bvid'], row['title'], json.loads(row['season']))
        except Exception as exc:
            error = str(exc) if isinstance(exc, PublicationOptionsError) else '合集关联请求失败，请稍后重试或到创作中心核对'
            queue.save_season_result(path, error)
        else:
            queue.save_season_result(path)
    return queue.get_by_path(path)
