"""Publishing defaults are copied into a segment, never into its source profile."""
from __future__ import annotations

from pathlib import Path
import re
from string import Formatter


def validate_template(template: str) -> None:
    if not isinstance(template, str) or not template.strip() or len(template) > 200:
        raise ValueError('标题模板须为 1–200 个字符')
    for _, field, spec, conversion in Formatter().parse(template):
        if field is not None and (field not in {'title', 'streamer', 'room', 'date'} or spec or conversion):
            raise ValueError('标题模板仅支持 {title}、{streamer}、{room}、{date}')


def validate_metadata(title: str, description: str, tags: list[str], tid: int | None = None) -> None:
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 80:
        raise ValueError('标题须为 1–80 个字符')
    if not isinstance(description, str) or len(description) > 2000:
        raise ValueError('简介不能超过 2000 个字符')
    if (not isinstance(tags, list) or len(tags) > 10 or
            any(not isinstance(t, str) or not t.strip() or len(t.strip()) > 20 or re.search(r'[,，\n]', t) for t in tags)):
        raise ValueError('TAG 最多 10 个，每个须为 1–20 个字符，不能包含逗号或换行')
    if tid is not None and (type(tid) is not int or not 1 <= tid <= 10000):
        raise ValueError('分区须为正整数')


def defaults_for_segment(wb, rel: str, title: str, description='', tags=None, settings=None) -> dict:
    from bilive.config.settings import Settings
    from bilive.lifecycle.profiles import profile_for_source, room_from_source
    settings = wb.settings_provider() if wb.settings_provider else settings or Settings()
    profile = profile_for_source(wb.videos, rel)
    defaults = settings.publication
    template = profile.get('default_title_template') or defaults.title_template
    validate_template(template)
    room = room_from_source(rel) or ''
    match = re.search(r'20\d{2}-\d{2}-\d{2}', Path(rel).name)
    date = match[0] if match else ''
    rendered = template.format(title=title or '直播片段', streamer=profile.get('display_name') or Path(rel).parent.name,
                               room=room, date=date)
    # Existing nonempty profile fields override global defaults; optional fields
    # left blank continue to inherit, matching the profile editor's behavior.
    result = {'title': rendered, 'description': profile.get('default_description') or defaults.description or description,
              'tags': profile.get('default_tags') or defaults.tags or list(tags or []),
              'tid': profile.get('default_tid') or defaults.tid or settings.upload.tid}
    validate_metadata(**result)
    return result


def generate_default_cover(final: Path) -> Path:
    from bilive.media.process import run_media_command
    cover = final.with_name('cover.jpg')
    temp = final.with_name('cover.tmp.jpg')
    try:
        run_media_command(['ffmpeg', '-y', '-i', str(final), '-frames:v', '1',
                           '-vf', 'scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2',
                           '-q:v', '3', str(temp)], capture_output=True, check=True, timeout=60)
        if not temp.is_file() or temp.stat().st_size == 0:
            raise ValueError('默认封面生成失败')
        temp.replace(cover)
    finally:
        temp.unlink(missing_ok=True)
    return cover
