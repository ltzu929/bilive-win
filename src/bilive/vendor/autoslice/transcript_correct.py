# Copyright (c) 2024 bilive.
"""Lightweight post-ASR transcript correction for proper nouns and homophones.

Fail-soft by design: any API/schema problem keeps the original Whisper text.
Correction never changes timestamps and never invents new cue lines.
"""

from __future__ import annotations

import base64
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from bilive.vendor.autoslice.analysis_result import AnalysisResult, TranscriptSegment
from bilive.vendor.config import MIMO_BASE_URL, MIMO_MODEL, MIMO_TIMEOUT
from bilive.vendor.log.logger import scan_log


def _compact_lines(segments: list[TranscriptSegment], *, max_chars: int = 6000, timed: bool = False) -> str:
    lines: list[str] = []
    used = 0
    for index, segment in enumerate(segments):
        text = str(segment.text or "").strip()
        if not text:
            continue
        timing = f"[{segment.start:.2f}-{segment.end:.2f}s] " if timed else ""
        line = f"{index}\t{timing}{text}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _build_correct_prompt(
    segments: list[TranscriptSegment],
    *,
    artist: str = "",
    title: str = "",
    context: str = "",
    hotwords: list[str] | None = None,
    with_audio: bool = False,
) -> str:
    body = _compact_lines(segments, timed=with_audio)
    context_text = str(context or "").strip()
    if len(context_text) > 800:
        context_text = context_text[:797] + "..."
    return (
        "你是直播字幕校对员。下面 ASR 字幕可能把主播名、游戏名、梗名或同音字听错。\n"
        + ("附带原音频与字幕对应，从 0 秒开始。请实际复听音频核对专名；"
           "方括号是每行对应的音频时间。只修改名单中专名的错写，保留其他字词。\n" if with_audio else "")
        +
        f"主播: {artist or 'unknown'}\n"
        f"标题线索: {title or '(none)'}\n"
        f"弹幕关键词线索: {context_text or '(none)'}\n\n"
        f"已确认专名（JSON 名称列表）: {json.dumps(hotwords or [], ensure_ascii=False)}\n"
        "专名列表仅供核对正确写法；只有发音和上下文吻合时才修正，"
        "不要因列表强行替换普通词；多个候选无法确定时保留原文。\n\n"
        "只修正专有名词、明显同音错字和专名写法；不要改时间，不要增删行，"
        "不要润色口语，不要把原意改掉。拿不准就不要改。\n"
        "每行格式为「索引\\t原句」。返回严格 JSON：\n"
        '{"corrections":[{"index":0,"text":"修正后整句"}]}\n'
        "无需修改时返回 {\"corrections\":[]}。\n\n"
        "字幕:\n"
        f"{body}"
    )


def _keep_name_edits(original: str, proposed: str, hotwords: list[str]) -> str:
    """Apply separable name edits while retaining all surrounding ASR text."""
    spans = []
    for name in dict.fromkeys(hotwords):
        if not name:
            continue
        offset = 0
        while (start := proposed.find(name, offset)) >= 0:
            spans.append((start, start + len(name)))
            offset = start + len(name)
    parts = []
    for tag, a, b, c, d in SequenceMatcher(None, original, proposed, autojunk=False).get_opcodes():
        if tag != 'equal' and c < d and any(start <= c and d <= end for start, end in spans):
            parts.append(proposed[c:d])
        else:
            parts.append(original[a:b])
    return ''.join(parts)


def _extract_json(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def correct_transcript_segments(
    segments: list[TranscriptSegment],
    *,
    artist: str = "",
    title: str = "",
    context: str = "",
    hotwords: list[str] | None = None,
    audio_path: str | Path | None = None,
    model: str = MIMO_MODEL,
    base_url: str = MIMO_BASE_URL,
    timeout: float = MIMO_TIMEOUT,
    client_factory: Callable[..., Any] = OpenAI,
) -> list[TranscriptSegment]:
    """Return a new segment list with text-only corrections applied."""
    usable = [segment for segment in segments if str(segment.text or "").strip()]
    if not usable:
        return list(segments)

    api_key = os.environ.get("MIMO_API_KEY")
    if not api_key:
        scan_log.warning("Subtitle correction skipped: MIMO_API_KEY is not set")
        return list(segments)

    try:
        prompt = _build_correct_prompt(usable, artist=artist, title=title, context=context,
                                       hotwords=hotwords, with_audio=audio_path is not None)
        content: Any = prompt
        if audio_path is not None:
            audio = Path(audio_path)
            # Base64 adds a third; the provider accepts at most 50 MB encoded.
            if audio.stat().st_size > 37_000_000:
                raise ValueError('subtitle correction audio exceeds provider limit')
            encoded = base64.b64encode(audio.read_bytes()).decode('ascii')
            content = [{'type': 'input_audio', 'input_audio': {'data': 'data:audio/wav;base64,' + encoded}},
                       {'type': 'text', 'text': prompt}]
        client = client_factory(api_key=api_key, base_url=base_url)
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是谨慎的直播字幕校对。只修正专名和明显听错，"
                        "输出严格 JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": content,
                },
            ],
            max_completion_tokens=2048,
            timeout=timeout,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        if not completion.choices:
            raise ValueError("correction response has no choices")
        content = str(getattr(completion.choices[0].message, "content", "") or "")
        parsed = _extract_json(content)
        if parsed is None:
            raise ValueError("correction JSON parse failed")
        raw_corrections = parsed.get("corrections", [])
        if raw_corrections is None:
            raw_corrections = []
        if not isinstance(raw_corrections, list):
            raise ValueError("corrections must be an array")
    except Exception as exc:
        scan_log.warning(f"Subtitle correction failed; keeping original ASR: {exc}")
        return list(segments)

    # Compact-line indices only cover non-empty rows; map them back to
    # the original ``segments`` list positions.
    usable_indices = [
        index
        for index, segment in enumerate(segments)
        if str(segment.text or "").strip()
    ]
    index_map = {
        compact_index: original_index
        for compact_index, original_index in enumerate(usable_indices)
    }

    corrected = list(segments)
    applied = 0
    for item in raw_corrections:
        if not isinstance(item, dict):
            continue
        try:
            compact_index = int(item.get("index"))
            text = str(item.get("text") or "").strip()
        except (TypeError, ValueError):
            continue
        if not text or compact_index not in index_map:
            continue
        original_index = index_map[compact_index]
        if audio_path is not None and hotwords:
            text = _keep_name_edits(segments[original_index].text, text, hotwords)
        if text != corrected[original_index].text:
            corrected[original_index] = TranscriptSegment(
                start=corrected[original_index].start,
                end=corrected[original_index].end,
                text=text,
            )
            applied += 1

    if applied:
        scan_log.info(f"Subtitle correction applied to {applied} cue(s)")
    return corrected


def correct_analysis_subtitles(
    analysis: AnalysisResult,
    *,
    artist: str = "",
    context: str = "",
    **kwargs: Any,
) -> AnalysisResult:
    """Correct ``analysis.transcript_segments`` in place and return it."""
    if not analysis.transcript_segments:
        return analysis
    analysis.transcript_segments = correct_transcript_segments(
        analysis.transcript_segments,
        artist=artist,
        title=analysis.title,
        context=context,
        **kwargs,
    )
    if analysis.transcript_segments:
        analysis.transcript = " ".join(
            segment.text.strip()
            for segment in analysis.transcript_segments
            if str(segment.text or "").strip()
        ).strip()
    return analysis
