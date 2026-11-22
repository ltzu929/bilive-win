"""成片工件：裁剪与烧录（规格 §4 阶段 7，§5 版本/工件）。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from bilive.media.subtitle_store import SubtitleStore
from bilive.media.process import run_media_command
from bilive.media.cut_timeline import kept_ranges, output_time


class PipelineError(Exception):
    pass


@dataclass
class FinalizeResult:
    segment_id: str
    raw_path: str
    final_path: str
    ass_path: str
    revision: int
    duration: float
    source_start: float
    source_end: float
    ok: bool = True
    logs: list[str] = field(default_factory=list)


def _run_ffmpeg(cmd: list[str], log: list[str]) -> None:
    proc = run_media_command(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    log.append(" ".join(cmd))
    if proc.returncode != 0:
        log.append(proc.stderr[-2000:])
        raise PipelineError(f"ffmpeg failed rc={proc.returncode}")
    if proc.stderr:
        log.append(proc.stderr[-500:])


def verify_media(path: Path) -> None:
    """部分 ffmpeg 输出不得记成功：存在 + 大小>0 + ffprobe 可读。"""
    if not path.is_file() or path.stat().st_size <= 0:
        raise PipelineError(f"partial or missing output: {path}")
    from bilive.media.remux import RemuxError, verify_mp4
    try:
        verify_mp4(path, [])
    except RemuxError as exc:
        raise PipelineError(str(exc)) from exc


def _video_encode_options() -> list[str]:
    from bilive.config.settings import load_settings
    encoder = load_settings().video.encoder
    if encoder == "h264_nvenc":
        # CQ is specific to NVENC; it is not numerically equivalent to x264 CRF.
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr",
                "-cq", "26", "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p"]


def ensure_raw_cut(
    source: Path,
    raw_out: Path,
    start: float,
    end: float,
    log: list[str],
    *, cuts=(),
) -> Path:
    """原始裁剪（无字幕）。成功裁剪保留供重试复用。"""
    if raw_out.is_file() and raw_out.stat().st_size > 0:
        verify_media(raw_out)
        return raw_out
    if end <= start:
        raise PipelineError("invalid source range")
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = raw_out.with_suffix(".partial.mp4")
    limit = ["-t", f"{end - start:.3f}"]
    media_input = ["-i", str(source)]
    window = media_input + limit
    filters = []
    graph_file = tmp.with_suffix('.ffgraph')
    if cuts:
        # Stop decoding at the selected out-point even if later frames would
        # all be discarded by select. Plain continuous cuts keep their output limit.
        window = limit + media_input
        kept = kept_ranges(end - start, cuts)
        # Keep video on the original presentation clock minus the removed time.
        # Rounding each span to whole frames before concatenation would accumulate
        # drift against the sample-accurate audio and subtitle clock.
        select = '+'.join(f'gte(t,{a:.6f})*lt(t,{b:.6f})' for a, b in kept)
        offset = '+'.join(f'{b-a:.6f}*gte(T,{b:.6f})' for a, b in cuts)
        graph = [f"[0:v:0]select='{select}',setpts='PTS-({offset})/TB'[v]"]
        inputs = []
        for i, (a, b) in enumerate(kept):
            audio = f"[0:a:0]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS"
            fade = min(.008, (b-a)/2)
            if a > 0:
                audio += f",afade=t=in:d={fade:.6f}"
            if b < end-start:
                audio += f",afade=t=out:st={b-a-fade:.6f}:d={fade:.6f}"
            graph.append(f"{audio}[a{i}]")
            inputs.append(f"[a{i}]")
        graph.append(f"{''.join(inputs)}concat=n={len(kept)}:v=0:a=1[a]")
        # A long manual selection can exceed the Windows command-line limit.
        graph_file.write_text(";".join(graph), encoding="ascii")
        filters = ["-filter_complex_script", str(graph_file), "-map", "[v]", "-map", "[a]", "-fps_mode:v", "vfr"]
    try:
        _run_ffmpeg(["ffmpeg", "-y", "-ss", f"{start:.3f}", *window, *filters,
                     *_video_encode_options(), "-c:a", "aac", str(tmp)], log)
    finally:
        if cuts:
            graph_file.unlink(missing_ok=True)
    verify_media(tmp)
    tmp.replace(raw_out)
    return raw_out


def burn_subtitles(
    raw: Path,
    ass: Path,
    final_out: Path,
    log: list[str],
) -> Path:
    """正式字幕烧录。部分输出不得记成功。"""
    if not ass.is_file():
        raise PipelineError("subtitle file missing; save subtitles first")
    final_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = final_out.with_suffix(".partial.mp4")
    # 路径中的特殊字符用 filename= 传给 subtitles filter
    ass_arg = str(ass).replace("\\", "/").replace(":", "\\:")
    _run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-i", str(raw),
            "-vf", f"subtitles=filename='{ass_arg}'",
            *_video_encode_options(),
            "-c:a", "copy",
            str(tmp),
        ],
        log,
    )
    verify_media(tmp)
    tmp.replace(final_out)
    return final_out


class Finalizer:
    """finalize / reburn / render 业务（Worker 内执行，不在 API 请求里跑）。"""

    def __init__(self, videos_root: Path, artifacts_root: Path, subtitle_store: SubtitleStore) -> None:
        self.videos_root = videos_root
        self.artifacts_root = artifacts_root
        self.subtitles = subtitle_store

    def artifact_paths(self, segment_id: str) -> dict[str, Path]:
        base = self.artifacts_root / segment_id
        return {
            "raw": base / "raw_candidate.mp4",
            "ass": base / "subtitle.ass",
            "final": base / "final_output.mp4",
            "meta": base / "finalize_meta.json",
        }

    def _segment(self, segment_id):
        path = self.artifacts_root / 'segments' / f'{segment_id}.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}

    @staticmethod
    def _raw_for_range(raw, source, relpath, start, end, cuts, log):
        meta = raw.with_suffix('.range.json')
        key = {'source': relpath, 'start': start, 'end': end}
        if cuts:
            key['cuts'] = cuts
        if not meta.is_file() or json.loads(meta.read_text(encoding='utf-8')) != key:
            fresh = raw.with_name('raw_next.mp4')
            fresh.unlink(missing_ok=True)
            ensure_raw_cut(source, fresh, start, end, log, cuts=cuts)
            meta.unlink(missing_ok=True)
            fresh.replace(raw)
            temp_meta = meta.with_suffix('.json.tmp')
            temp_meta.write_text(json.dumps(key), encoding='utf-8')
            temp_meta.replace(meta)
        return ensure_raw_cut(source, raw, start, end, log, cuts=cuts)

    def finalize(
        self,
        segment_id: str,
        source_relpath: str,
        source_start: float,
        source_end: float,
        revision: int,
    ) -> FinalizeResult:
        source = (self.videos_root / source_relpath).resolve()
        if not source.is_relative_to(self.videos_root.resolve()):
            raise PipelineError("source path escapes videos root")
        if not source.is_file():
            raise PipelineError(f"source missing: {source_relpath}")

        paths = self.artifact_paths(segment_id)
        log: list[str] = []
        sub = self.subtitles.load(segment_id)
        if not sub["lines"]:
            raise PipelineError("subtitles not saved; save before finalize")
        if sub.get("revision", 0) != revision:
            raise PipelineError(
                f"revision mismatch: payload={revision} server={sub.get('revision')}"
            )

        duration = source_end - source_start
        from bilive.media.remux import _ffprobe_duration
        import math
        if not all(math.isfinite(x) for x in (source_start, source_end)) or source_start < 0 or duration <= 0:
            raise PipelineError("invalid source range")
        if source_end > _ffprobe_duration(source, log) + 0.01:
            raise PipelineError("range exceeds source duration")
        segment = self._segment(segment_id)
        cuts = segment.get('chat_cuts', [])
        self._raw_for_range(paths['raw'], source, source_relpath, source_start, source_end, cuts, log)
        appearance = segment.get('subtitle_style', {})
        self.subtitles.write_ass(segment_id, paths["ass"], appearance, cuts=cuts)
        burn_subtitles(paths["raw"], paths["ass"], paths["final"], log)
        duration = output_time(duration, cuts)

        meta = {
            "segment_id": segment_id,
            "source_relpath": source_relpath,
            "source_start": source_start,
            "source_end": source_end,
            "revision": revision,
            "subtitle_style": appearance,
            "duration": duration,
            "chat_cuts": cuts,
            "raw_path": str(paths["raw"]),
            "final_path": str(paths["final"]),
            "ass_path": str(paths["ass"]),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "log": log[-20:],
        }
        paths["meta"].write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return FinalizeResult(
            segment_id=segment_id,
            raw_path=str(paths["raw"]),
            final_path=str(paths["final"]),
            ass_path=str(paths["ass"]),
            revision=revision,
            duration=duration,
            source_start=source_start,
            source_end=source_end,
            logs=log,
        )

    def reburn(
        self,
        segment_id: str,
        source_relpath: str,
        source_start: float,
        source_end: float,
        revision: int,
    ) -> FinalizeResult:
        """字幕/样式修改后重烧；同路径重烧确实更新字幕。旧成片在此步骤只记历史路径。"""
        paths = self.artifact_paths(segment_id)
        if paths["final"].is_file():
            history = paths["final"].with_name("final_output.history.json")
            prev = []
            if history.is_file():
                prev = json.loads(history.read_text(encoding="utf-8"))
            prev.append(
                {
                    "path": str(paths["final"]),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
            history.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.finalize(segment_id, source_relpath, source_start, source_end, revision)

    def render_only(
        self,
        segment_id: str,
        source_relpath: str,
        source_start: float,
        source_end: float,
    ) -> Path:
        source = (self.videos_root / source_relpath).resolve()
        paths = self.artifact_paths(segment_id)
        log: list[str] = []
        return self._raw_for_range(paths['raw'], source, source_relpath, source_start, source_end,
                                   self._segment(segment_id).get('chat_cuts', []), log)
