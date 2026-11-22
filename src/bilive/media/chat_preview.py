"""Versioned, local chat-edit previews, independent of the staged final."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from bilive.domain.models import task_id_from_source_relpath
from bilive.jobs.action_jobs import ActionConflict
from bilive.media.chat_trim import chat_kind, detect_chat_cuts
from bilive.media.finalize import burn_subtitles, ensure_raw_cut


def selected_cuts(draft):
    return [[c['start'], c['end']] for c in draft['cuts'] if c['enabled']]


def preview_path(artifacts, segment, identifier):
    if not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise ValueError('精剪预览编号无效')
    # The existing source-package lifecycle also recycles temporary previews.
    task = task_id_from_source_relpath(segment['source_relpath'])
    return artifacts / 'candidates' / task / 'chat-preview' / (identifier + '.mp4')


def require_draft(segment, identifier):
    draft = segment.get('chat_draft') or {}
    if not identifier or identifier != draft.get('id') or draft.get('revision') != segment['revision']:
        raise ActionConflict('精剪预览已更新，请刷新后查看当前版本')
    return draft


def build_preview(wb, job):
    sid = job.target
    folder = wb.artifacts / sid
    revision = job.payload['revision']
    with wb.segment_lock(sid):
        segment = wb.load(sid)
        dest = preview_path(wb.artifacts, segment, job.job_id)
        wb.check_revision(segment, revision)
        if segment.get('publish_approval') or segment.get('state') == 'dropped':
            raise ActionConflict('当前片段已锁定')
        if chat_kind(segment, folder) == 'other':
            raise ValueError('当前片段不属于聊天精剪范围')
        previous = segment.get('chat_draft') or {}
        if previous.get('id') == job.job_id and dest.is_file():
            return {'ok': True, 'preview_id': job.job_id}
        saved = wb.subtitles.load(sid)
        if saved['revision'] != revision or segment.get('subtitles_revision') != revision or not saved['lines']:
            raise ActionConflict('请先保存当前片段字幕')
        source = wb.source(segment['source_relpath'])
        if 'toggle' in job.payload:
            draft = require_draft(segment, job.payload['preview_id'])
            proposals = [dict(c) for c in draft['cuts']]
            index = job.payload['toggle']
            if type(index) is not int or not 0 <= index < len(proposals):
                raise ValueError('请选择已有的剪点')
            proposals[index]['enabled'] = not proposals[index]['enabled']
        else:
            proposals = detect_chat_cuts(source, segment, saved['lines'])
            restored = {(c['start'], c['end']) for c in previous.get('cuts', []) if not c['enabled']}
            for cut in proposals:
                cut['enabled'] = (cut['start'], cut['end']) not in restored
        draft = {'id': job.job_id, 'revision': revision, 'cuts': proposals}
        cuts = selected_cuts(draft)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=dest.parent) as work:
            temp = Path(work)
            video = temp / 'preview.mp4'
            if cuts == segment.get('chat_cuts', []) and wb.valid_final(segment):
                shutil.copyfile(segment['final_path'], video)
            else:
                log = []
                raw = ensure_raw_cut(source, temp/'raw.mp4', segment['source_start'], segment['source_end'], log, cuts=cuts)
                ass = wb.subtitles.write_ass(sid, temp/'preview.ass', segment.get('subtitle_style'), cuts=cuts)
                burn_subtitles(raw, ass, video, log)
            video.replace(dest)
        with wb.lock():
            current = wb.load(sid)
            wb.check_revision(current, revision)
            current['chat_draft'] = draft
            wb.save(current)
        if previous.get('id') and previous['id'] != job.job_id:
            try:
                preview_path(wb.artifacts, segment, previous['id']).unlink(missing_ok=True)
            except OSError:
                pass  # A Windows player can still hold it; source recycling collects it later.
        return {'ok': True, 'preview_id': job.job_id, 'cut_count': len(cuts)}
