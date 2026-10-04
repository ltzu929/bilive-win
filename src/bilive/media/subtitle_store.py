"""字幕服务端已保存行（与浏览器草稿分开，规格 §2、§6）。"""

from __future__ import annotations

import json
from pathlib import Path

from bilive.domain.models import SubtitleLine, clamp_subtitle_times


class SubtitleStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, segment_id: str) -> Path:
        return self.root / f"{segment_id}.json"

    def save(
        self,
        segment_id: str,
        duration: float,
        lines: list[SubtitleLine],
        revision: int,
        expected_revision: int | None = None,
    ) -> dict:
        """字幕绑定 segment 版本 revision。

        finalize/reburn 必须携带同一 revision，避免旧编辑覆盖新版本。
        expected_revision 用于校验服务端当前版本，冲突时拒绝保存。
        """
        current = self.load(segment_id)
        if expected_revision is not None and current["revision"] != expected_revision:
            raise ValueError(
                f"revision conflict: server={current['revision']} expected={expected_revision}"
            )
        clamp_subtitle_times(lines, duration)
        payload = {
            "segment_id": segment_id,
            "duration": duration,
            "revision": revision,
            "lines": [
                {"index": ln.index, "start": ln.start, "end": ln.end, "text": ln.text}
                for ln in lines
            ],
        }
        tmp = self._path(segment_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path(segment_id))
        return payload

    def load(self, segment_id: str) -> dict:
        path = self._path(segment_id)
        if not path.is_file():
            return {"segment_id": segment_id, "revision": 0, "lines": [], "duration": 0.0}
        return json.loads(path.read_text(encoding="utf-8"))

    def write_ass(self, segment_id: str, out_path: Path, appearance: dict | None = None) -> Path:
        data = self.load(segment_id)
        if not data["lines"]:
            raise ValueError("no saved subtitles to write")
        from bilive.vendor.subtitle_style import SubtitleStyle, validate_style
        validate_style(appearance or {})
        a = SubtitleStyle.from_mapping(appearance)
        style = f"Style: Default,{a.font_name},{a.font_size},{a.primary_colour or '&H00FFFFFF'},&H000000FF,{a.outline_colour or '&H00000000'},&H64000000,0,0,0,0,100,100,0,0,1,{a.outline if a.outline is not None else 1},0,{a.alignment or 2},10,10,{a.margin_v},1"
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1920",
            "PlayResY: 1080",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            style,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        def ts(sec: float) -> str:
            if sec < 0:
                sec = 0
            h = int(sec // 3600)
            m = int((sec % 3600) // 60)
            s = sec % 60
            return f"{h}:{m:02d}:{s:05.2f}"

        for ln in data["lines"]:
            text = ln["text"].replace("\n", "\\N")
            lines.append(
                f"Dialogue: 0,{ts(ln['start'])},{ts(ln['end'])},Default,,0,0,0,,{text}"
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path
