"""Pinned streamer profile and recommendation rules; persistence lock adapted."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
import json, os, re, threading, uuid
from pathlib import Path
from typing import Any, Callable
from bilive.worker.locks import ProcessLock
from bilive.vendor.subtitle_style import SubtitleStyle
VALID_CONTENT_EXPERIENCE_TYPES = frozenset({'positive','negative','missed_segment_positive'})
_TASK_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_ROOM_ID_RE = re.compile(r'^\d+$')
def _state_lock(path):
    return ProcessLock(path.with_suffix(path.suffix + '.lock'))
def state_root(videos_root: str | Path) -> Path:
    return Path(videos_root).expanduser().resolve() / ".bilive-state"


def streamer_state_dir(videos_root: str | Path) -> Path:
    return state_root(videos_root) / "streamers"


def streamer_state_path(videos_root: str | Path, room_id: str) -> Path:
    return streamer_state_dir(videos_root) / f"{_validate_room_id(room_id)}.json"


def _validate_room_id(room_id: str) -> str:
    identifier = str(room_id or "").strip()
    if not _ROOM_ID_RE.fullmatch(identifier):
        raise ValueError("Invalid room id")
    return identifier


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('invalid persisted streamer state; repair explicitly') from exc
    if not isinstance(payload, dict):
        raise ValueError('invalid persisted streamer state')
    return payload


def _experience_path(videos_root: str | Path) -> Path:
    return state_root(videos_root) / "experiences.jsonl"


def read_experiences(
    videos_root: str | Path,
    *,
    room_id: str | None = None,
) -> list[dict[str, Any]]:
    path = _experience_path(videos_root)
    if not path.is_file():
        return []
    requested_room = str(room_id) if room_id is not None else None
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if requested_room is not None and str(record.get("room_id") or "") != requested_room:
            continue
        records.append(record)
    return records


def append_experience(
    videos_root: str | Path,
    record: dict[str, Any],
) -> dict[str, Any]:
    path = _experience_path(videos_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    dedupe_key = str(record.get("dedupe_key") or "")
    with _state_lock(path):
        existing = read_experiences(videos_root)
        if dedupe_key:
            for item in existing:
                if str(item.get("dedupe_key") or "") == dedupe_key:
                    return item
        payload = dict(record)
        payload.setdefault("experience_id", uuid.uuid4().hex)
        payload.setdefault("created_at", _now())
        with path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload


def streamer_evidence_summary(
    videos_root: str | Path,
    room_id: str,
    *,
    min_samples: int = 5,
) -> dict[str, Any]:
    """Return the read-only evidence gate shown before recommendations."""
    room = _validate_room_id(room_id)
    required = max(1, int(min_samples))
    experiences = read_experiences(videos_root, room_id=room)
    valid = [
        item
        for item in experiences
        if item.get("experience_type") in VALID_CONTENT_EXPERIENCE_TYPES
    ]
    positive = [item for item in valid if item.get("conclusion") == "positive"]
    negative = [item for item in valid if item.get("conclusion") == "negative"]
    ready = len(valid) >= required and bool(positive) and bool(negative)
    return {
        "evidence_status": "ready" if ready else "insufficient_evidence",
        "sample_size": len(valid),
        "positive_count": len(positive),
        "negative_count": len(negative),
        "minimum_samples": required,
        "message": (
            "证据已达到主播建议生成条件"
            if ready
            else f"证据不足：至少需要 {required} 条有效人工样本，且同时包含正、负样本"
        ),
    }


def generate_streamer_recommendation(
    videos_root: str | Path,
    room_id: str,
    *,
    min_samples: int = 5,
) -> dict[str, Any]:
    room = _validate_room_id(room_id)
    experiences = read_experiences(videos_root, room_id=room)
    valid = [
        item
        for item in experiences
        if item.get("experience_type") in VALID_CONTENT_EXPERIENCE_TYPES
    ]
    positive = [
        item
        for item in valid
        if item.get("conclusion") == "positive"
    ]
    negative = [
        item
        for item in valid
        if item.get("conclusion") == "negative"
    ]
    base = streamer_evidence_summary(
        videos_root,
        room,
        min_samples=min_samples,
    )
    if base["evidence_status"] != "ready":
        return base

    reason_counts = Counter(
        str(item.get("reason_type") or "content_review")
        for item in negative + [
            item for item in valid if item.get("experience_type") == "missed_segment_positive"
        ]
    )
    reasons = [item for item, _count in reason_counts.most_common(3)]
    guidance = "；".join(reasons) if reasons else "保留完整主题、发展和落点"
    guidance = f"人工样本提示：重点检查 {guidance}。"
    evidence_ids = [str(item.get("experience_id") or "") for item in valid]

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        recommendations = data.get("recommendations")
        if not isinstance(recommendations, list):
            recommendations = []
        for item in recommendations:
            if isinstance(item, dict) and item.get("evidence_ids") == evidence_ids:
                return data
        recommendations.append(
            {
                "recommendation_id": uuid.uuid4().hex,
                "room_id": room,
                "created_at": _now(),
                "status": "pending",
                "evidence_status": "ready",
                "sample_size": len(valid),
                "positive_count": len(positive),
                "negative_count": len(negative),
                "evidence_ids": evidence_ids,
                "basis": [
                    {
                        "experience_id": item.get("experience_id"),
                        "task_id": item.get("task_id"),
                        "source_rel_path": item.get("source_rel_path"),
                        "segment_id": item.get("segment_id"),
                        "start_seconds": item.get("start_seconds"),
                        "end_seconds": item.get("end_seconds"),
                        "experience_type": item.get("experience_type"),
                        "decision": item.get("conclusion"),
                        "conclusion": item.get("conclusion"),
                        "reason_type": item.get("reason_type"),
                        "reason": item.get("reason"),
                        "note": item.get("note"),
                    }
                    for item in valid
                ],
                "changes": {"approved_guidance": guidance},
                "message": "建议只影响该主播，需人工点击应用",
            }
        )
        data["recommendations"] = recommendations
        return data

    current = read_streamer_state(videos_root, room) or {
        "room_id": room,
        "profile": default_streamer_profile(room),
        "recommendations": [],
    }
    return mutate_streamer_state(videos_root, room, mutate)


def default_streamer_profile(room_id: str) -> dict[str, Any]:
    return {
        "room_id": _validate_room_id(room_id),
        "display_name": "",
        "aliases": [],
        "default_tags": [],
        "default_description": "",
        "default_slice_options": {},
        "default_subtitle_style": {},
        "approved_guidance": "",
        "updated_at": "",
    }


def read_streamer_state(
    videos_root: str | Path,
    room_id: str,
) -> dict[str, Any] | None:
    return _read_json(streamer_state_path(videos_root, room_id))


def read_streamer_profile(
    videos_root: str | Path,
    room_id: str,
) -> dict[str, Any]:
    room = _validate_room_id(room_id)
    state = read_streamer_state(videos_root, room)
    profile = state.get("profile") if isinstance(state, dict) else None
    if not isinstance(profile, dict):
        return default_streamer_profile(room)
    return {**default_streamer_profile(room), **profile}


def read_streamer_recommendations(
    videos_root: str | Path,
    room_id: str,
) -> list[dict[str, Any]]:
    state = read_streamer_state(videos_root, room_id) or {}
    recommendations = state.get("recommendations")
    return [dict(item) for item in recommendations if isinstance(item, dict)] if isinstance(recommendations, list) else []


def mutate_streamer_state(
    videos_root: str | Path,
    room_id: str,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    room = _validate_room_id(room_id)
    path = streamer_state_path(videos_root, room)
    with _state_lock(path):
        current = _read_json(path) or {
            "room_id": room,
            "profile": default_streamer_profile(room),
            "recommendations": [],
            "updated_at": _now(),
        }
        updated = mutator(dict(current))
        updated["room_id"] = room
        updated["updated_at"] = _now()
        _atomic_write(path, updated)
        return updated


def patch_streamer_profile(
    videos_root: str | Path,
    room_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    room = _validate_room_id(room_id)
    if not isinstance(payload, dict):
        raise ValueError("request body must be an object")
    allowed = {
        "display_name",
        "aliases",
        "default_tags",
        "default_description",
        "default_slice_options",
        "default_subtitle_style",
        "approved_guidance",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown profile fields: {', '.join(unknown)}")

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        profile = {**default_streamer_profile(room), **(state.get("profile") or {})}
        if "display_name" in payload:
            profile["display_name"] = str(payload.get("display_name") or "").strip()
        for key in ("aliases", "default_tags"):
            if key not in payload:
                continue
            value = payload.get(key)
            if not isinstance(value, list):
                raise ValueError(f"{key} must be an array")
            profile[key] = [str(item).strip() for item in value if str(item).strip()]
        for key in ("default_description", "approved_guidance"):
            if key in payload:
                profile[key] = str(payload.get(key) or "")
        if "default_slice_options" in payload:
            profile["default_slice_options"] = _normalize_slice_options(
                payload.get("default_slice_options")
            )
        if "default_subtitle_style" in payload:
            profile["default_subtitle_style"] = _normalize_subtitle_style(
                payload.get("default_subtitle_style")
            )
        profile["updated_at"] = _now()
        state["profile"] = profile
        return state

    return mutate_streamer_state(videos_root, room, mutate).get("profile", {})


def apply_streamer_recommendation(
    videos_root: str | Path,
    room_id: str,
    recommendation_id: str,
) -> dict[str, Any]:
    room = _validate_room_id(room_id)
    identifier = str(recommendation_id or "").strip()
    if not _TASK_ID_RE.fullmatch(identifier):
        raise ValueError("Invalid recommendation id")

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        recommendations = state.get("recommendations")
        if not isinstance(recommendations, list):
            raise FileNotFoundError("Recommendation not found")
        recommendation = next(
            (
                item
                for item in recommendations
                if isinstance(item, dict)
                and str(item.get("recommendation_id") or "") == identifier
            ),
            None,
        )
        if recommendation is None:
            raise FileNotFoundError("Recommendation not found")
        profile = {**default_streamer_profile(room), **(state.get("profile") or {})}
        if str(recommendation.get("status") or "") != "applied":
            changes = recommendation.get("changes") or {}
            if "approved_guidance" in changes:
                profile["approved_guidance"] = str(
                    changes.get("approved_guidance") or ""
                )
            if isinstance(changes.get("default_slice_options"), dict):
                profile["default_slice_options"] = _normalize_slice_options(
                    changes["default_slice_options"]
                )
            profile["updated_at"] = _now()
            recommendation["status"] = "applied"
            recommendation["applied_at"] = _now()
            state["profile"] = profile
        return state

    state = mutate_streamer_state(videos_root, room, mutate)
    profile = {**default_streamer_profile(room), **(state.get("profile") or {})}
    recommendation = next(
        item
        for item in state.get("recommendations", [])
        if isinstance(item, dict)
        and str(item.get("recommendation_id") or "") == identifier
    )
    return {"profile": profile, "recommendation": recommendation}


def profile_slice_options(
    videos_root: str | Path,
    room_id: str,
) -> dict[str, Any]:
    profile = read_streamer_profile(videos_root, room_id)
    value = profile.get("default_slice_options")
    return dict(value) if isinstance(value, dict) else {}


def profile_subtitle_style(
    videos_root: str | Path,
    room_id: str,
) -> dict[str, Any]:
    profile = read_streamer_profile(videos_root, room_id)
    value = profile.get("default_subtitle_style")
    return dict(value) if isinstance(value, dict) else {}


def profile_context_for_mimo(
    videos_root: str | Path,
    room_id: str,
) -> str:
    profile = read_streamer_profile(videos_root, room_id)
    guidance = str(profile.get("approved_guidance") or "").strip()
    if not guidance:
        return ""
    return guidance[:2000]


def _normalize_slice_options(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("default_slice_options must be an object")
    allowed = {
        "burst_ratio",
        "burst_window",
        "burst_context",
        "burst_merge_gap",
        "burst_top_n",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unknown default_slice_options: {', '.join(unknown)}")
    result: dict[str, Any] = {}
    for key, raw in value.items():
        if key == "burst_ratio":
            number = _number(raw)
            if number is None or not 1.5 <= number <= 8:
                raise ValueError("default burst_ratio must be 1.5-8")
            result[key] = number
        else:
            try:
                number = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"default {key} must be an integer") from exc
            limits = {
                "burst_window": (5, 30),
                "burst_context": (30, 120),
                "burst_merge_gap": (0, 30),
                "burst_top_n": (1, 5),
            }
            minimum, maximum = limits[key]
            if not minimum <= number <= maximum:
                raise ValueError(f"default {key} must be {minimum}-{maximum}")
            result[key] = number
    return result


def _normalize_subtitle_style(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("default_subtitle_style must be an object")
    from bilive.vendor.subtitle_style import validate_style
    validate_style(value)

    return SubtitleStyle.from_mapping(value).to_mapping()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
