"""Fine-cut intervals use seconds relative to the original selected clip."""
from __future__ import annotations

import math


def kept_ranges(duration, cuts):
    kept = []
    cursor = 0.0
    for start, end in cuts:
        if not (math.isfinite(start) and math.isfinite(end) and cursor <= start < end <= duration):
            raise ValueError('精剪区间与当前片段不匹配')
        if start > cursor:
            kept.append((cursor, start))
        cursor = end
    if cursor < duration:
        kept.append((cursor, duration))
    if not kept:
        raise ValueError('精剪后片段为空')
    return kept


def output_time(source_time, cuts):
    return source_time - sum(max(0, min(source_time, end) - start) for start, end in cuts)


def output_subtitles(lines, cuts):
    result = []
    for line in lines:
        start = output_time(line['start'], cuts)
        end = output_time(line['end'], cuts)
        if end > start + 1e-6:
            result.append({**line, 'start': start, 'end': end})
    return result
