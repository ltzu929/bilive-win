"""Pinned sentence-boundary and duplicate rules, namespace/API adapted."""
from bilive.vendor.autoslice.analysis_result import TrimSuggestion, TranscriptSegment
from bilive.domain.models import segment_id_from_source_and_range
import re
import math

def segment_id_for(source, start, end):
    return segment_id_from_source_and_range(source, round(start*1000), round(end*1000))
def snap_trim_to_segments(
    trim: TrimSuggestion,
    segments: list[TranscriptSegment],
    tolerance: float,
    *,
    outward_only: bool = False,
) -> TrimSuggestion:
    """Snap trim endpoints to the nearest sentence boundary within ``tolerance``.

    ``trim`` and ``segments`` are both relative to the candidate start.
    Endpoints without a boundary within ``tolerance`` seconds stay put.
    Returns the original trim if snapping would empty or reverse it.
    """
    if not segments or tolerance <= 0:
        return trim
    starts = [seg.start for seg in segments]
    ends = [seg.end for seg in segments]
    if outward_only:
        new_start = _snap_start_outward(float(trim.trim_start), starts, tolerance)
        new_end = _snap_end_outward(float(trim.trim_end), ends, tolerance)
    else:
        new_start = _snap_value(float(trim.trim_start), starts, tolerance)
        new_end = _snap_value(float(trim.trim_end), ends, tolerance)
    if new_end <= new_start:
        return trim
    return TrimSuggestion(
        trim_start=new_start,
        trim_end=new_end,
        reason=trim.reason,
    )

def _snap_value(value: float, candidates: list[float], tolerance: float) -> float:
    best = value
    best_dist = tolerance
    for candidate in candidates:
        dist = abs(candidate - value)
        if dist <= best_dist:
            best_dist = dist
            best = candidate
    return best

def _snap_start_outward(value: float, candidates: list[float], tolerance: float) -> float:
    eligible = [
        candidate
        for candidate in candidates
        if candidate <= value and value - candidate <= tolerance
    ]
    return max(eligible, default=value)

def _snap_end_outward(value: float, candidates: list[float], tolerance: float) -> float:
    eligible = [
        candidate
        for candidate in candidates
        if candidate >= value and candidate - value <= tolerance
    ]
    return min(eligible, default=value)

def _slice_segments_to_trim(
    segments: list[TranscriptSegment],
    trim: TrimSuggestion,
) -> tuple[str, list[TranscriptSegment]]:
    """Offset candidate-relative segments into trim-relative segments.

    Segments overlapping ``[trim_start, trim_end]`` are clipped to the window
    and shifted so they are relative to ``trim_start``.
    """
    start = float(trim.trim_start)
    end = float(trim.trim_end)
    window = end - start
    out: list[TranscriptSegment] = []
    for seg in segments:
        if seg.end <= start or seg.start >= end:
            continue
        new_start = max(0.0, seg.start - start)
        new_end = min(window, seg.end - start)
        if new_end <= new_start:
            continue
        out.append(
            TranscriptSegment(start=new_start, end=new_end, text=seg.text)
        )
    transcript = " ".join(seg.text for seg in out).strip()
    return transcript, out

def _mark_cross_candidate_duplicates(items, source_path, route_below_quality_gate_to_review):
    """Route lower-scored overlapping, text-similar clips to manual review."""
    entries = []
    for index, candidate, precomputed in items:
        if precomputed.get("error") is not None:
            continue
        for result in precomputed.get("results") or []:
            if route_below_quality_gate_to_review(result):
                continue
            trim = getattr(result, "suggested_trim", None)
            if (
                result.judge_status != "keep"
                or not result.retain_recommendation
                or trim is None
            ):
                continue
            try:
                start = float(candidate.context_start) + float(trim.trim_start)
                end = float(candidate.context_start) + float(trim.trim_end)
                valid = math.isfinite(start) and math.isfinite(end) and candidate.context_start <= start < end <= candidate.context_end + 0.01
            except (ValueError, TypeError):
                valid = False
            if not valid:
                result.judge_status = 'review'
                result.retain_recommendation = False
                result.judge_error = 'invalid judgment interval'
                continue
            entries.append(
                {
                    "index": index,
                    "result": result,
                    "start": start,
                    "end": end,
                    "score": _quality_rank(result),
                    "text": str(result.topic_summary or result.title or ""),
                }
            )

    winners = []
    for entry in sorted(
        entries,
        key=lambda item: (
            -item["score"],
            item["start"],
            item["end"],
            item["index"],
        ),
    ):
        duplicate = next(
            (
                winner
                for winner in winners
                if _intervals_are_duplicate(entry, winner)
                and _text_similarity(entry["text"], winner["text"]) >= 0.50
            ),
            None,
        )
        if duplicate is None:
            entry["result"]._dedupe_key = segment_id_for(
                source_path,
                entry["start"],
                entry["end"],
            )
            winners.append(entry)
            continue
        result = entry["result"]
        duplicate_id = getattr(
            duplicate["result"],
            "_dedupe_key",
            segment_id_for(
                source_path,
                duplicate["start"],
                duplicate["end"],
            ),
        )
        reason = (
            "Duplicate clip routed to review: overlaps a higher-scored clip "
            f"({duplicate_id})"
        )
        result.judge_status = "review"
        result.retain_recommendation = False
        result.judge_error = reason
        result.quality_reason = (
            f"{result.quality_reason}; {reason}"
            if result.quality_reason
            else reason
        )
        result.duplicate_of = duplicate_id
        result.duplicate_reason = reason

def _quality_rank(result):
    def score(value):
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    return (
        0.4 * score(result.quality_score)
        + 0.4 * score(result.completeness_score)
        + 0.2 * score(result.confidence)
    )

def _intervals_are_duplicate(left, right):
    overlap = max(
        0.0,
        min(left["end"], right["end"]) - max(left["start"], right["start"]),
    )
    if overlap <= 0:
        return False
    left_duration = left["end"] - left["start"]
    right_duration = right["end"] - right["start"]
    union = left_duration + right_duration - overlap
    iou = overlap / union if union > 0 else 0.0
    shorter_coverage = overlap / min(left_duration, right_duration)
    return iou >= 0.60 or shorter_coverage >= 0.80

def _text_similarity(left, right):
    def grams(value):
        normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", value.lower())
        if len(normalized) < 2:
            return {normalized} if normalized else set()
        return {normalized[index : index + 2] for index in range(len(normalized) - 1)}

    left_grams = grams(str(left or ""))
    right_grams = grams(str(right or ""))
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)
