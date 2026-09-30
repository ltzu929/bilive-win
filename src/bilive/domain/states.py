"""状态机与合法迁移（规格 §3）。"""

from __future__ import annotations

from bilive.domain.models import (
    ActionJobStatus,
    JudgeDecision,
    RecordingTaskStatus,
    TrashStatus,
    UploadStage,
)

RECORDING_TRANSITIONS: dict[str, set[str]] = {
    RecordingTaskStatus.PENDING: {
        RecordingTaskStatus.PROCESSING,
        RecordingTaskStatus.DONE,
        RecordingTaskStatus.FAILED,
    },
    RecordingTaskStatus.PROCESSING: {
        RecordingTaskStatus.DONE,
        RecordingTaskStatus.FAILED,
    },
    RecordingTaskStatus.DONE: set(),
    RecordingTaskStatus.FAILED: {RecordingTaskStatus.PENDING},
}

ACTION_TRANSITIONS: dict[str, set[str]] = {
    ActionJobStatus.PENDING: {ActionJobStatus.PROCESSING},
    ActionJobStatus.PROCESSING: {
        ActionJobStatus.DONE,
        ActionJobStatus.FAILED,
        ActionJobStatus.PENDING,  # 失去 owner 的合法 processing 可回 pending
    },
    ActionJobStatus.DONE: set(),
    ActionJobStatus.FAILED: set(),
}

UPLOAD_TRANSITIONS: dict[str, set[str]] = {
    UploadStage.STAGED: {UploadStage.QUEUED},
    UploadStage.QUEUED: {UploadStage.UPLOADING},
    UploadStage.UPLOADING: {
        UploadStage.UPLOADED,
        UploadStage.FAILED,
        UploadStage.QUEUED,
    },
    UploadStage.UPLOADED: {
        UploadStage.PUBLISHING,
        UploadStage.FAILED,
        UploadStage.UPLOADED,
    },
    UploadStage.PUBLISHING: {
        UploadStage.PUBLISHED,
        UploadStage.FAILED,
        UploadStage.UPLOADED,
    },
    UploadStage.PUBLISHED: set(),
    UploadStage.FAILED: {UploadStage.QUEUED, UploadStage.UPLOADED},
}

AUTO_CONTINUE_DECISIONS = {JudgeDecision.KEEP}
MANUAL_REQUIRED_DECISIONS = {
    JudgeDecision.MANUAL_KEEP,
    JudgeDecision.REVIEW,
    JudgeDecision.JUDGE_FAILED,
}


def can_transition_recording(old: str, new: str) -> bool:
    return new in RECORDING_TRANSITIONS.get(old, set())


def can_transition_action(old: str, new: str) -> bool:
    return new in ACTION_TRANSITIONS.get(old, set())


def can_transition_upload(old: str, new: str) -> bool:
    return new in UPLOAD_TRANSITIONS.get(old, set())


def is_terminal_action(status: str) -> bool:
    return status in {
        ActionJobStatus.DONE.value,
        ActionJobStatus.FAILED.value,
    }


def manual_retry_allowed(stage: str, remote_filename: str | None) -> bool:
    """页面手动 retry 仅接受 failed 且无 remote_filename。"""
    return stage == UploadStage.FAILED.value and not remote_filename


def activation_target_stage(has_remote_filename: bool) -> str:
    """已激活已有 remote_filename 的 staged 行可直接进入 uploaded。"""
    return UploadStage.UPLOADED.value if has_remote_filename else UploadStage.QUEUED.value


def decision_continues_to_auto_pipeline(decision: str, scores: dict[str, float] | None, is_duplicate: bool) -> bool:
    """质量门：三项分别达标且非跨候选重复 → 自动成片；否则人工。"""
    if is_duplicate:
        return False
    if decision not in AUTO_CONTINUE_DECISIONS:
        return False
    if not scores:
        return False
    needed = ("quality", "completeness", "confidence")
    return all(scores.get(k) is not None and scores[k] >= 0.80 for k in needed)


def trash_status_after_move(item_ok: list[bool]) -> tuple[str, str | None]:
    """冻结清单逐项结果 → trash_status。无法确认已移动项时停止，不猜测成功。"""
    if all(item_ok):
        return TrashStatus.DONE.value, None
    if any(item_ok):
        return TrashStatus.FAILED.value, "partial_move"
    return TrashStatus.FAILED.value, "unknown_result_stop"
