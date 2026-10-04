"""录制转封装（规格 §4 阶段1、§5）：验证 MP4 成功才删 FLV。"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


class RemuxError(Exception):
    pass


@dataclass
class RemuxResult:
    source_flv: str
    mp4_path: str
    verified: bool = False
    deleted_flv: bool = False
    duration: float | None = None
    log: list[str] = field(default_factory=list)


def _ffprobe_duration(path: Path, log: list[str]) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log.append(f"ffprobe {path.name} rc={proc.returncode}")
    if proc.returncode != 0:
        raise RemuxError(f"ffprobe failed: {proc.stderr[-500:]}")
    data = json.loads(proc.stdout or "{}")
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise RemuxError("duration missing or zero")
    return duration


def _probe_has_av(path: Path, log: list[str]) -> None:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log.append(f"ffprobe streams {path.name} rc={proc.returncode}")
    if proc.returncode != 0:
        raise RemuxError(f"stream probe failed: {proc.stderr[-500:]}")
    kinds = {s.get("codec_type") for s in json.loads(proc.stdout or "{}").get("streams", [])}
    if "video" not in kinds or "audio" not in kinds:
        raise RemuxError(f"missing audio/video stream: {kinds}")


def verify_mp4(path: Path, log: list[str]) -> float:
    """不能以非空 MP4 代替验证：音视频流 + 时长 + 首中尾解码。"""
    if not path.is_file() or path.stat().st_size <= 0:
        raise RemuxError("mp4 missing or empty")
    _probe_has_av(path, log)
    duration = _ffprobe_duration(path, log)
    # 首中尾解码检查
    for label, ts in (("start", 0.0), ("mid", duration / 2), ("end", max(duration - 0.2, 0.0))):
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v", "error",
                "-ss", f"{ts:.3f}",
                "-i", str(path),
                "-frames:v", "1",
                "-f", "null",
                "-",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log.append(f"decode[{label}] rc={proc.returncode}")
        if proc.returncode != 0:
            raise RemuxError(f"decode check failed at {label}: {proc.stderr[-300:]}")
    return duration


def remux_recording(flv_path: Path, delete_source: bool = True) -> RemuxResult:
    """FLV → MP4 转封装。验证成功才删 FLV；失败保留源和失败任务。"""
    flv_path = flv_path.resolve()
    mp4_path = flv_path.with_suffix('.mp4')
    if not flv_path.is_file():
        # Crash after verified replace + FLV removal: finish the same queued action.
        if mp4_path.is_file():
            log = []
            duration = verify_mp4(mp4_path, log)
            return RemuxResult(str(flv_path), str(mp4_path), verified=True,
                               deleted_flv=True, duration=duration, log=log)
        raise RemuxError(f"source flv missing: {flv_path}")
    result = RemuxResult(source_flv=str(flv_path), mp4_path=str(mp4_path))
    tmp = mp4_path.with_suffix(".partial.mp4")

    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(flv_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(tmp),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    result.log.append(f"ffmpeg remux rc={proc.returncode}")
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RemuxError(f"remux failed: {proc.stderr[-800:]}")

    try:
        result.duration = verify_mp4(tmp, result.log)
    except RemuxError:
        tmp.unlink(missing_ok=True)
        raise

    tmp.replace(mp4_path)
    result.verified = True

    if delete_source:
        # 验证成功才删 FLV（规格：delete_source=never 由 Worker 接管后执行）
        flv_path.unlink()
        result.deleted_flv = True
    return result
