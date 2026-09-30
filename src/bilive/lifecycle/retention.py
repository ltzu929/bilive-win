"""源录播生命周期与到期回收（规格 §3.4、§5）。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from bilive.domain.models import RecordingLifecycle, ReviewState, TrashStatus
from bilive.domain.states import trash_status_after_move

WARN_DAY = 5
EXPIRE_DAY = 7
DAY = 86400.0


@dataclass
class LifecycleStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, source_relpath: str) -> Path:
        safe = source_relpath.replace("/", "_").replace("\\", "_")
        return self.root / f"{safe}.json"

    def load(self, source_relpath: str) -> RecordingLifecycle | None:
        path = self._path(source_relpath)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return RecordingLifecycle(
            source_relpath=data["source_relpath"],
            review_state=ReviewState(data["review_state"]),
            retention_started_at=data.get("retention_started_at"),
            deadline=data.get("deadline"),
            trash_status=TrashStatus(data["trash_status"]) if data.get("trash_status") else None,
            frozen_manifest=data.get("frozen_manifest", []),
            trash_results=data.get("trash_results", []),
        )

    def ensure(self, source_relpath: str, now: float | None = None) -> RecordingLifecycle:
        existing = self.load(source_relpath)
        if existing is not None:
            # 不得每次查询重新延后已有期限
            return existing
        now = time.time() if now is None else now
        # 初次日期优先由源文件名解析；无法解析则用创建记录时当前时间
        parsed = _parse_time_from_name(source_relpath)
        started = parsed if parsed is not None else now
        rec = RecordingLifecycle(
            source_relpath=source_relpath,
            retention_started_at=started,
            deadline=started + EXPIRE_DAY * DAY,
        )
        self.save(rec)
        return rec

    def save(self, rec: RecordingLifecycle) -> None:
        path = self._path(rec.source_relpath)
        payload = {
            "source_relpath": rec.source_relpath,
            "review_state": rec.review_state.value,
            "retention_started_at": rec.retention_started_at,
            "deadline": rec.deadline,
            "trash_status": rec.trash_status.value if rec.trash_status else None,
            "frozen_manifest": rec.frozen_manifest,
            "trash_results": rec.trash_results,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _parse_time_from_name(source_relpath: str) -> float | None:
    import re

    name = Path(source_relpath).name
    # 例如 2026-09-23T21-10-00.flv / 2026-09-23 21-10-00.mp4
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})[T _](\d{2})-(\d{2})-(\d{2})", name)
    if not m:
        return None
    import datetime

    dt = datetime.datetime(
        int(m.group(1)), int(m.group(2)), int(m.group(3)),
        int(m.group(4)), int(m.group(5)), int(m.group(6)),
        tzinfo=datetime.timezone.utc,
    )
    return dt.timestamp()


@dataclass
class RetentionPlanner:
    """第5天预警、第7天到期排 force_expired 回收。"""

    store: LifecycleStore

    def evaluate(self, source_relpath: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        rec = self.store.ensure(source_relpath, now=now)
        assert rec.deadline is not None and rec.retention_started_at is not None
        age_days = (now - rec.retention_started_at) / DAY
        warn = age_days >= WARN_DAY and not self._expired(rec, now)
        expired = self._expired(rec, now)
        return {
            "source_relpath": source_relpath,
            "age_days": round(age_days, 3),
            "warn_day5": warn,
            "expired_day7": expired,
            "deadline": rec.deadline,
            "review_state": rec.review_state.value,
            "action": "force_expired" if expired else ("warn" if warn else "keep"),
        }

    def _expired(self, rec: RecordingLifecycle, now: float) -> bool:
        return rec.deadline is not None and now >= rec.deadline

    def plan_trash(self, source_relpath: str, protected: list[str], now: float | None = None) -> dict:
        """到期维护：冻结清单 + force_expired；保护成片与审核记录。"""
        plan = self.evaluate(source_relpath, now=now)
        if not plan["expired_day7"]:
            return {**plan, "queued": False, "reason": "not_expired"}
        rec = self.store.ensure(source_relpath, now=now)
        root = Path(source_relpath).parent
        # 冻结源文件包清单（仅源录播相关，保护成片）
        manifest = [
            str(root / Path(source_relpath).with_suffix(".mp4").name),
            str(root / Path(source_relpath).with_suffix(".xml").name),
        ]
        rec.frozen_manifest = [p for p in manifest if Path(p).name not in set(protected)]
        rec.review_state = ReviewState.TRASH_PENDING
        rec.trash_status = TrashStatus.PENDING
        self.store.save(rec)
        return {
            **plan,
            "queued": True,
            "action": "force_expired",
            "frozen_manifest": rec.frozen_manifest,
            "protected": protected,
        }


def apply_trash_results(
    store: LifecycleStore,
    source_relpath: str,
    item_results: list[dict],
) -> dict:
    """逐项移动结果；无法确认已移动项时停止，不猜测成功。"""
    rec = store.load(source_relpath)
    if rec is None:
        raise ValueError("lifecycle record missing")
    oks = [bool(r.get("ok")) for r in item_results]
    if any(not r.get("ok") and r.get("status") == "unknown" for r in item_results):
        status, reason = trash_status_after_move([False] * len(oks))
    else:
        status, reason = trash_status_after_move(oks)
    rec.trash_results = item_results
    rec.trash_status = TrashStatus(status)
    store.save(rec)
    return {
        "trash_status": status,
        "reason": reason,
        "review_state": rec.review_state.value,
        "results": item_results,
    }
