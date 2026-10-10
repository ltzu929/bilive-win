"""领域对象与字段语义（规格 §2）。"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class JudgeDecision(str, Enum):
    KEEP = "keep"
    MANUAL_KEEP = "manual_keep"
    REVIEW = "review"
    JUDGE_FAILED = "judge_failed"
    DROP = "drop"


class RecordingTaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class ActionJobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class UploadStage(str, Enum):
    CANCELLED = "cancelled"
    STAGED = "staged"
    QUEUED = "queued"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class ReviewState(str, Enum):
    UNPROCESSED = "unprocessed"
    PROCESSING = "processing"
    SOURCE_REVIEW = "source_review"
    CANDIDATE_REVIEW = "candidate_review"
    REVIEW_COMPLETE = "review_complete"
    TRASH_PENDING = "trash_pending"


class TrashStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


class UploadRetrySource(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"


def task_id_from_source_relpath(relpath: str) -> str:
    """task_id = 源相对路径的 URL-safe Base64（旧身份兼容）。"""
    raw = relpath.replace("\\", "/").encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def source_relpath_from_task_id(task_id: str) -> str:
    pad = "=" * (-len(task_id) % 4)
    return base64.urlsafe_b64decode(task_id + pad).decode("utf-8")


_SEGMENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def segment_id_from_source_and_range(
    source_relpath: str, start_ms: int, end_ms: int
) -> str:
    """旧 segment_id：源路径 + 毫秒精度区间派生，不是内容哈希。"""
    base = f"{source_relpath.replace(chr(92), '/') }|{start_ms}-{end_ms}"
    digest = base64.urlsafe_b64encode(base.encode("utf-8")).decode("ascii").rstrip("=")
    return digest


@dataclass
class SourceTimes:
    """源录播绝对秒。"""

    start: float
    end: float


@dataclass
class CandidateWindow:
    """候选区间：源区间 + 密度核心 + 上下文窗。"""

    source_relpath: str
    source_start: float
    source_end: float
    core_start: float
    core_end: float
    danmaku_count: int
    candidate_path: str | None = None


@dataclass
class Segment:
    segment_id: str
    source_relpath: str
    source_start: float
    source_end: float
    decision: JudgeDecision
    quality_score: float | None
    completeness_score: float | None
    confidence_score: float | None
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    revision: int = 1
    candidate_id: str | None = None
    note: str = ""

    @property
    def has_scores(self) -> bool:
        return (
            self.quality_score is not None
            and self.completeness_score is not None
            and self.confidence_score is not None
        )


@dataclass
class SubtitleLine:
    """字幕行：时间相对片段起点。"""

    index: int
    start: float
    end: float
    text: str

    def validate(self) -> None:
        import math
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise ValueError("subtitle times must be finite")
        if not self.text or not self.text.strip():
            raise ValueError("subtitle text empty")
        if self.start < 0 or self.end < 0:
            raise ValueError("subtitle times must be non-negative")
        if not (self.end > self.start):
            raise ValueError("subtitle end must be > start")


@dataclass
class AnalysisSidecar:
    segment_id: str
    judgment: dict[str, Any]
    quality: dict[str, float]
    trim: dict[str, float]
    transcript: str | None
    segments_ts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FinalArtifact:
    """成片工件：raw_candidate / analysis_sidecar / final_output 分路径。"""

    segment_id: str
    kind: str
    path: str
    valid: bool = True
    created_at: str = ""


@dataclass
class ActionJob:
    job_id: str
    action: str
    target: str
    payload: dict[str, Any]
    created_at: str
    owner: str | None = None
    status: ActionJobStatus = ActionJobStatus.PENDING
    progress: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None

    def payload_fingerprint(self) -> str:
        import hashlib
        import json

        raw = json.dumps(self.payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class UploadRow:
    video_path: str
    stage: UploadStage = UploadStage.STAGED
    remote_filename: str | None = None
    bvid: str | None = None
    attempts: int = 0
    next_attempt_at: float | None = None
    last_error: str | None = None
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class RecordingLifecycle:
    source_relpath: str
    review_state: ReviewState = ReviewState.UNPROCESSED
    retention_started_at: float | None = None
    deadline: float | None = None
    trash_status: TrashStatus | None = None
    frozen_manifest: list[str] = field(default_factory=list)
    trash_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StreamerProfile:
    room_id: int
    display_name: str
    aliases: list[str] = field(default_factory=list)
    default_tags: list[str] = field(default_factory=list)
    default_description: str = ""
    density_params: dict[str, Any] = field(default_factory=dict)
    subtitle_style: dict[str, Any] = field(default_factory=dict)
    approved_guidance: str = ""


def clamp_subtitle_times(
    lines: list[SubtitleLine], duration: float, tolerance: float = 0.01
) -> list[SubtitleLine]:
    """超过片段时长 0.01s 拒绝；容差内钳回并再次验证。"""
    for line in lines:
        line.validate()
        if line.end > duration + tolerance:
            raise ValueError(f"subtitle line {line.index} exceeds segment duration")
        if line.end > duration:
            line.end = duration
            line.validate()
    return lines
