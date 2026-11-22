"""Frame selection and local composition for an unapproved posting draft."""
import base64
import hashlib
import os
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from bilive.domain.models import task_id_from_source_relpath
from bilive.media.cut_timeline import output_time
from bilive.media.process import run_media_command


class Crop(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    x: float = Field(default=.5, ge=0, le=1)
    y: float = Field(default=.5, ge=0, le=1)
    zoom: float = Field(default=1, ge=1, le=5)


class CoverDesign(BaseModel):
    model_config = ConfigDict(extra='forbid')
    frame_ms: int | None = Field(default=None, ge=0)
    primary: Crop = Field(default_factory=Crop)
    secondary: Crop | None = None
    caption: str = Field(default='', max_length=32)
    upload: str | None = Field(default=None, max_length=2_800_000)


def frame_file(folder, ms):
    return folder / 'cover-frames' / f'{ms}.jpg'


def capture_frames(wb, job):
    sid = job.target
    with wb.segment_lock(sid):
        s = wb.load(sid)
        wb.check_revision(s, job.payload['revision'])
        at = job.payload['source_time']
        start, end = s['source_start'], s['source_end']
        if not start <= at < end:
            raise ValueError('取帧时间已超出片段范围')
        folder = wb.artifacts / sid
        cuts = []
        try:
            path, origin = wb.source(s['source_relpath']), 0
        except FileNotFoundError:
            path = Path(s.get('candidate_path') or '').resolve()
            candidate_root = wb.artifacts / 'candidates' / task_id_from_source_relpath(s['source_relpath'])
            if (path.is_relative_to(candidate_root) and path.suffix == '.mp4' and path.is_file()
                    and s['candidate_start'] <= start and end <= s['candidate_end']):
                origin = s['candidate_start']
            elif wb.valid_final(s) and Path(s['final_path']).resolve().is_relative_to(folder):
                path, origin = Path(s['final_path']), start
                cuts = s.get('chat_cuts', [])
            else:
                raise ValueError('原录播、候选及当前成片均不可用于取帧') from None
        frames = []
        for t in sorted(set(round((at + delta) * 1000) for delta in (-.5, 0, .5))):
            if not start <= t / 1000 < end:
                continue
            relative = t / 1000 - origin
            if any(a <= relative < b for a, b in cuts):
                continue
            dest = frame_file(folder, t)
            dest.parent.mkdir(parents=True, exist_ok=True)
            original = dest.with_suffix('.png')
            if not original.is_file():
                tmp = dest.with_suffix('.tmp.png')
                try:
                    run_media_command(['ffmpeg', '-loglevel', 'error', '-y', '-ss', f'{output_time(relative, cuts):.6f}',
                                       '-i', str(path), '-frames:v', '1', str(tmp)],
                                      check=True, capture_output=True, timeout=45)
                    if not tmp.is_file() or not tmp.stat().st_size:
                        continue
                    tmp.replace(original)
                finally:
                    tmp.unlink(missing_ok=True)
            if not dest.is_file():
                tmp = dest.with_suffix('.tmp.jpg')
                try:
                    with Image.open(original) as image:
                        image.thumbnail((1920, image.height), Image.Resampling.LANCZOS)
                        image.convert('RGB').save(tmp, quality=94)
                    tmp.replace(dest)
                finally:
                    tmp.unlink(missing_ok=True)
            frames.append(t)
        if not frames:
            raise ValueError('该位置没有可解码的画面，请稍微向前移动')
        return {'frames': frames, 'revision': s['revision']}


def _panel(img, size, crop):
    scale = max(size[0] / img.width, size[1] / img.height) * crop.zoom
    w, h = size[0] / scale, size[1] / scale
    x = min(max(0, crop.x * img.width - w / 2), max(0, img.width - w))
    y = min(max(0, crop.y * img.height - h / 2), max(0, img.height - h))
    return img.resize(size, Image.Resampling.LANCZOS, box=(x, y, min(img.width, x+w), min(img.height, y+h)))


def render_cover(folder, segment, design):
    if design.upload is not None:
        if design.frame_ms is not None:
            raise ValueError('请选择一种图片来源')
        try:
            data = base64.b64decode(design.upload, validate=True)
            with Image.open(BytesIO(data)) as uploaded:
                if uploaded.format not in {'JPEG', 'PNG', 'WEBP'} or uploaded.width * uploaded.height > 16_000_000:
                    raise ValueError('封面图片格式或尺寸不适用')
                image = ImageOps.fit(ImageOps.exif_transpose(uploaded).convert('RGB'), (960, 540))
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError('封面图片无法读取') from exc
    else:
        ms = design.frame_ms
        if ms is None or not segment['source_start'] <= ms / 1000 < segment['source_end']:
            raise ValueError('请在当前片段内选帧')
        with Image.open(frame_file(folder, ms)) as source:
            rgb = source.convert('RGB')
        if design.secondary:
            image = Image.new('RGB', (960, 540), 'white')
            image.paste(_panel(rgb, (510, 540), design.primary), (0, 0))
            image.paste(_panel(rgb, (446, 540), design.secondary), (514, 0))
        else:
            image = _panel(rgb, (960, 540), design.primary)
        if design.caption.strip():
            draw = ImageDraw.Draw(image)
            font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'msyhbd.ttc'
            caption = ' '.join(design.caption.splitlines()).strip()
            for size in range(64, 16, -2):
                font = ImageFont.truetype(str(font_path) if font_path.is_file() else 'DejaVuSans-Bold.ttf', size)
                if draw.textlength(caption, font=font) <= 888:
                    break
            draw.text((36, 506), caption, anchor='ls', fill='white',
                      font=font, stroke_width=max(2, size // 14), stroke_fill='#212036')
    out = BytesIO()
    image.save(out, 'JPEG', quality=92, optimize=True)
    return out.getvalue()


def save_cover(folder, segment, design):
    data = render_cover(folder, segment, design)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('cover-' + hashlib.sha256(data).hexdigest() + '.jpg')
    if not path.is_file():
        tmp = path.with_suffix('.tmp')
        tmp.write_bytes(data)
        tmp.replace(path)
    segment['cover_path'] = str(path)
    segment['cover_design'] = design.model_dump(exclude={'upload'}) | {'uploaded': design.upload is not None}
    segment['selected_cover'] = str(path)
