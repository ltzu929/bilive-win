"""Independent queue consumer; every network operation is outside DB transactions."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from bilive.config.secrets import redact
from bilive.worker.locks import ProcessLock, LockUnavailable


class UploadConsumer:
    def __init__(self, queue, settings, client, lock_path: Path, status_path: Path | None = None,
                 segments_root: Path | None = None):
        self.queue, self.settings, self.client = queue, settings, client
        self.lock_path, self.status_path = lock_path, status_path
        self.segments_root = segments_root

    def process_one(self) -> dict:
        lock = ProcessLock(self.lock_path)
        try:
            lock.acquire()
        except LockUnavailable:
            return {"status":"busy"}
        try:
            self.queue.recover_interrupted()
            return self._process_one()
        finally:
            lock.release()

    def _process_one(self) -> dict:
        row = self.queue.peek_next()
        if row is None:
            return self._status({"status":"idle"})
        path = row["video_path"]
        try:
            login = self.client.check_login()
        except Exception:
            login = None
        if not login or login.get("isLogin") is not True:
            self.queue.defer_auth(path, time.time()+self.settings.auth_retry)
            return self._status({"status":"auth_paused"})
        claimed = self.queue.claim_next(self.settings.max_attempts)
        if claimed is None:
            return self._status({"status":"idle"})
        path = claimed["video_path"]
        metadata = {"copyright":1,"title":claimed["title"],"desc":claimed["description"],
                    "tag":",".join(json.loads(claimed["tags"])),"tid":claimed.get('tid') or self.settings.tid}
        if not metadata["title"].strip():
            self.queue.mark_failed(path, "upload title required")
            return self._status({"status":"failed","error":"upload title required"})
        try:
            topic = json.loads(claimed['topic']) if claimed.get('topic') else None
            season = json.loads(claimed['season']) if claimed.get('season') else None
            if topic or season:
                options = self.client.publication_options.validate_options(topic, season)
                metadata['topic'] = options['topic']
            if self.segments_root:
                # Only trust the segment that owns this exact final artifact.
                sid = Path(path).parent.name
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,240}', sid):
                    raise ValueError('投稿来源片段标识无效')
                segment = json.loads((self.segments_root / f'{sid}.json').read_text(encoding='utf-8'))
                if Path(segment.get('final_path') or '').resolve() != Path(path).resolve():
                    raise ValueError('投稿成片与来源片段不匹配')
                source = str(segment['source_relpath']).replace('\\', '/')
                room = re.match(r'^(\d+)(?:\s*-|_|/)', source)
                if room is None:
                    room = re.match(r'blive_(\d+)_', source.rsplit('/', 1)[-1])
                if room is None:
                    raise ValueError('无法识别录播房间号，请先核对投稿来源')
                metadata['source'] = f'https://live.bilibili.com/{room[1]}'
            remote = claimed["remote_filename"]
            if not remote:
                if not Path(path).is_file():
                    raise FileNotFoundError("current final missing")
                remote = self.client.upload_file(path, metadata)
                if not isinstance(remote,str) or not remote.strip():
                    raise ValueError("CDN result has no remote filename")
                self.queue.mark_uploaded(path,remote)
            # Confirmed remote filename is reused on every publication retry.
            cover_url = claimed.get('cover_url')
            if claimed.get('cover_path') and not cover_url:
                cover_url = self.client.upload_cover(claimed['cover_path'])
                if not isinstance(cover_url, str) or not cover_url.startswith(('http://', 'https://', '//')):
                    raise ValueError('默认封面上传未返回有效地址')
                self.queue.save_cover_url(path, cover_url)
            if cover_url:
                metadata['cover'] = cover_url
            self.queue.mark_publishing(path)
            response = self.client.submit_uploaded_video(remote,metadata)
            if not isinstance(response,dict):
                raise RuntimeError('B 站投稿返回格式无效，请核对创作中心投稿记录')
            if response.get('code') != 0:
                code = response.get('code', '缺失')
                message = response.get('message') or response.get('msg') or '未返回错误说明'
                raise RuntimeError(f'B 站投稿失败（错误码 {code}）：{str(message)[:500]}')
            data = response.get("data")
            bvid = data.get("bvid") if isinstance(data,dict) else None
            if not isinstance(bvid,str) or not bvid.strip():
                raise RuntimeError('B 站返回投稿成功但缺少 BV 号，请核对创作中心投稿记录')
            aid = data.get('aid')
            self.queue.mark_published(path,bvid,aid if type(aid) is int and aid > 0 else None)
        except Exception as exc:
            message = redact(str(exc))
            for value in getattr(self.client,"cookies",{}).values():
                if value:
                    message = message.replace(value,"[redacted]")
            outcome = self.queue.schedule_retry(path,message,self.settings.max_attempts,
                self.settings.retry_base*(2**claimed["attempts"]),time.time())
            return self._status({"status":outcome["stage"],"error":message})
        cleanup_error = None
        season_result = None
        if season:
            from bilive.upload.season import join_published_season
            season_result = join_published_season(self.queue, self.client, path)
        if self.settings.delete_after_success:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                cleanup_error = "published; local cleanup failed"
        return self._status({"status":"published","bvid":bvid,"cleanup_error":cleanup_error,
                             'season_status': season_result.get('season_status') if season_result else None,
                             'season_error': season_result.get('season_error') if season_result else None})

    def _status(self, data):
        if self.status_path:
            self.status_path.parent.mkdir(parents=True,exist_ok=True)
            tmp = self.status_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({**data,"updated_at":time.time()}),encoding="utf-8")
            tmp.replace(self.status_path)
        return data

    def run(self):
        with ProcessLock(self.lock_path):
            self.queue.recover_interrupted()
            while not (self.lock_path.parent / "upload-stop").exists():
                self._process_one()
                time.sleep(self.settings.poll_interval)
