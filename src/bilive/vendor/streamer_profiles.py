"""Pinned streamer profile and recommendation rules; persistence lock adapted."""
from __future__ import annotations
from datetime import datetime, timezone
import json, os, re, threading, uuid
from pathlib import Path
from typing import Any, Callable
from bilive.worker.locks import ProcessLock
from bilive.vendor.subtitle_style import SubtitleStyle
VALID_CONTENT_EXPERIENCE_TYPES = frozenset({'positive','negative','missed_segment_positive'})
CORRECTION_TYPES = frozenset({'editorial_correction', 'boundary_correction', 'understanding_correction'})
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


def is_duplicate_experience(item: dict[str, Any]) -> bool:
    """Keep duplicate disposal separate from a judgment about content quality."""
    return bool(item.get('duplicate_of')) or any(
        str(item.get(key) or '').strip() in {'已切', '已切过'}
        for key in ('human_reason', 'drop_reason', 'reason_type', 'note')
    )


def _is_content_experience(item: dict[str, Any]) -> bool:
    return (item.get('experience_type') in VALID_CONTENT_EXPERIENCE_TYPES
            and not is_duplicate_experience(item)
            # Legacy content_review rows can simply mirror a model verdict and
            # explanation. Without human provenance they do not calibrate quality.
            and not (item.get('reason_type') == 'content_review'
                     and not item.get('human_decision') and not item.get('human_reason')
                     and not item.get('human_feedback')))


def latest_experiences(records: list[dict]) -> list[dict]:
    """Keep history in the log, but count only the latest judgment of a segment."""
    latest = {}
    for index, item in enumerate(records):
        key = (str(item.get('room_id')), str(item.get('segment_id') or f'row:{index}'))
        version = (int(item.get('revision') or 0), int(item.get('editorial_sequence') or 0), bool(item.get('classification')))
        previous = latest.get(key)
        if previous is None or version >= previous[0]:
            latest[key] = (version, index, item)
    return [entry[2] for entry in sorted(latest.values(), key=lambda entry: entry[1])]


def _recommendation_experiences(videos_root, room):
    return [item for item in latest_experiences(read_experiences(videos_root, room_id=room))
            if not is_duplicate_experience(item) and (
                _is_content_experience(item) or item.get('experience_type') in CORRECTION_TYPES
                or item.get('experience_type') == 'publication_preference')
            and (item.get('experience_type') != 'editorial_correction'
                 or item.get('human_reason') or item.get('editorial_reason')
                 or any(edit.get('action') in {'range', 'metadata'} and any(
                     edit.get('before', {}).get(key) != edit.get('after', {}).get(key)
                     for key in ('source_start', 'source_end', 'title', 'description'))
                     for edit in item.get('editorial_history', [])))]


def streamer_evidence_summary(
    videos_root: str | Path,
    room_id: str,
    *,
    min_samples: int = 5,
) -> dict[str, Any]:
    """Return the read-only evidence gate shown before recommendations."""
    room = _validate_room_id(room_id)
    required = max(1, int(min_samples))
    valid = _recommendation_experiences(videos_root, room)
    positive = [item for item in valid if item.get("conclusion") == "positive"]
    negative = [item for item in valid if item.get("conclusion") == "negative"]
    corrections = [item for item in valid if item.get('experience_type') in CORRECTION_TYPES]
    preferences = [item for item in valid if item.get('experience_type') == 'publication_preference']
    ready = len(valid) >= required and bool(positive) and bool(negative or corrections or preferences)
    return {
        "evidence_status": "ready" if ready else "insufficient_evidence",
        "sample_size": len(valid),
        "positive_count": len(positive),
        "negative_count": len(negative),
        "correction_count": len(corrections),
        "publication_preference_count": len(preferences),
        "minimum_samples": required,
        "message": (
            "证据已达到主播建议生成条件"
            if ready
            else f"证据不足：至少需要 {required} 条有效人工样本，包含保留样本及废弃或纠正依据"
        ),
    }


def generate_streamer_recommendation(
    videos_root: str | Path,
    room_id: str,
    *,
    min_samples: int = 5,
) -> dict[str, Any]:
    room = _validate_room_id(room_id)
    valid = _recommendation_experiences(videos_root, room)
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

    def example(item):
        # Older notes may be generated model explanations, not human reasons.
        reason = ('复盘补充：' + item['review_note'] if item.get('review_note') else
                  str(item.get('human_reason') or item.get('editorial_reason') or item.get('reason_type') or '').strip())
        if reason == 'content_review':
            reason = ''
        if reason and not item.get('human_reason') and str(item.get('note') or '').startswith(reason):
            reason = item['note']
        if not reason:
            for edit in reversed(item.get('editorial_history', [])):
                before, after = edit.get('before', {}), edit.get('after', {})
                changed = [f'{key}: {str(before.get(key))[:55]} → {str(after.get(key))[:55]}'
                           for key in ('source_start', 'source_end', 'title', 'description')
                           if before.get(key) != after.get(key) and key in before]
                if changed:
                    reason = '人工修改 ' + '；'.join(changed)
                    break
        title = str(item.get('title') or item.get('source_rel_path') or '人工样本')[:60]
        return f'- 《{title}》：{reason[:120] or "未填写人工理由，不推测原因"}'

    guidance = (
        "先还原谁发出弹幕/SC、主播如何回应、事件主线和具体观看价值，再定位必要铺垫和最后有效回应。"
        "完整、有情绪或弹幕多本身不是观看价值。核对音画，不把主动表演写成故障，不把引用当主播立场。"
        "保留语言短梗、视觉、声音及陪伴价值，不按关键词或固定时长一律删除；没有合适内容可不选。"
        "人工保留校准取舍，不证明原标题的事实；文案仍须核对当前片段。"
    )
    groups = [
        ('人工保留对照，不得因新规则误删', positive),
        ('人工内容废弃，仅核对相似问题，不扩大为话题禁令', negative),
        ('人工范围/理解纠正，与内容无聊分开', [item for item in valid if item.get('experience_type') in CORRECTION_TYPES]),
        ('发布取舍，与内容好坏分开，不自动推断所有争议内容禁用', [item for item in valid if item.get('experience_type') == 'publication_preference']),
    ]
    for label, items in groups:
        if items:
            # Reserve one example for the latest decision, even when historical
            # calibration has review notes. Keep a distinct calibrated control.
            selected = items[-1:]
            if len(items) > 1:
                controls = [item for item in items[:-1] if item.get('review_note')]
                selected.insert(0, controls[-1] if controls else items[-2])
            guidance += '\n' + label + '：\n' + '\n'.join(example(item) for item in selected)
    # The production judge reads at most 2000 characters; keep the complete candidate visible.
    guidance = guidance[:2000]
    evidence_ids = [str(item.get("experience_id") or "") for item in valid]

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        recommendations = data.get("recommendations")
        if not isinstance(recommendations, list):
            recommendations = []
        for item in recommendations:
            if (isinstance(item, dict) and item.get("evidence_ids") == evidence_ids
                    and item.get('changes', {}).get('approved_guidance') == guidance
                    and item.get('method_version') == 2
                    and item.get('status') in {'pending', 'applied'}):
                return data
        for item in recommendations:
            if isinstance(item, dict) and item.get('status') == 'pending':
                item['status'] = 'superseded'
        recommendations.append(
            {
                "recommendation_id": uuid.uuid4().hex,
                "room_id": room,
                "created_at": _now(),
                "status": "pending",
                "method_version": 2,
                "evidence_status": "ready",
                "sample_size": len(valid),
                "positive_count": len(positive),
                "negative_count": len(negative),
                "correction_count": base['correction_count'],
                "publication_preference_count": base['publication_preference_count'],
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
                        "human_reason": item.get("human_reason"),
                        "editorial_reason": item.get('editorial_reason'),
                        "title": item.get('title'),
                        "human_feedback": item.get('human_feedback'),
                        "human_decision": item.get('human_decision'),
                        "editorial_history": item.get('editorial_history', []),
                        "revision": item.get('revision'),
                        "classification": item.get('classification'),
                        "review_note": item.get('review_note'),
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
        "subtitle_hotwords": [],
        "default_tags": [],
        "default_description": "",
        "default_title_template": "",
        "default_tid": None,
        "default_season": None,
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
        "subtitle_hotwords",
        "default_tags",
        "default_description",
        "default_title_template",
        "default_tid",
        "default_season",
        "default_slice_options",
        "default_subtitle_style",
        "approved_guidance",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown profile fields: {', '.join(unknown)}")
    from bilive.media.publication import validate_template, validate_metadata
    if 'default_title_template' in payload and payload['default_title_template']:
        validate_template(payload['default_title_template'])
    validate_metadata('标题', payload.get('default_description', ''), payload.get('default_tags', []), payload.get('default_tid'))
    if 'default_season' in payload:
        from bilive.vendor.upload.publication_options import validate_selections
        validate_selections(season=payload['default_season'])

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
        if "subtitle_hotwords" in payload:
            value = payload["subtitle_hotwords"]
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError("字幕专名必须是字符串数组")
            names = []
            for item in value:
                name = item.strip()
                if any(char in name for char in "\r\n\x00"):
                    raise ValueError("每个字幕专名只能包含一个名称，请分行填写")
                if name and name not in names:
                    names.append(name)
            profile["subtitle_hotwords"] = names
        for key in ("default_description", "approved_guidance"):
            if key in payload:
                profile[key] = str(payload.get(key) or "")
        for key in ('default_title_template', 'default_tid', 'default_season'):
            if key in payload:
                profile[key] = payload[key]
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
            current_ids = [str(item.get('experience_id') or '') for item in _recommendation_experiences(videos_root, room)]
            if recommendation.get('status') != 'pending' or recommendation.get('evidence_ids') != current_ids:
                raise ValueError('建议依据已更新，请重新生成后核对')
            changes = recommendation.get("changes") or {}
            if "approved_guidance" in changes:
                recommendation['previous_guidance'] = profile.get('approved_guidance', '')
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

    # Profile styles are overrides: absent fields must keep inheriting the
    # global style, including when the user clears the override with {}.
    normalized = SubtitleStyle.from_mapping(value).to_mapping()
    return {key: normalized[key] for key in value}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
