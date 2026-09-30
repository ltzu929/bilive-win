"""配置解析：环境变量 > 配置文件 > 默认值。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_NAME = "bilive-server.toml"


@dataclass
class WorkerConfig:
    idle_timeout: int = 900
    idle_check_interval: int = 30


@dataclass
class VideoConfig:
    min_source_size_mb: float = 100.0


@dataclass
class BurstConfig:
    ratio: float = 3.0
    window: int = 10
    context: int = 120
    merge_gap: int = 5
    top_n: int = 3
    lag: int = 0


@dataclass
class MimoConfig:
    model: str = "mimo-v2.5"
    fps: int = 1
    media_resolution: str = "default"
    timeout: int = 180
    max_base64_bytes: int = 48_000_000
    request_concurrency: int = 3
    encode_concurrency: int = 1


@dataclass
class QualityGate:
    quality: float = 0.80
    completeness: float = 0.80
    confidence: float = 0.80

    def all_pass(self, quality: float, completeness: float, confidence: float) -> bool:
        return (
            quality >= self.quality
            and completeness >= self.completeness
            and confidence >= self.confidence
        )


@dataclass
class AnalysisConfig:
    pre_judge_asr: bool = True
    asr_prompt_max_chars: int = 4000


@dataclass
class SubtitleConfig:
    asr_model: str = "large-v3"
    asr_device: str = "cuda"
    asr_compute_type: str = "float16"
    asr_batch: int = 8
    asr_cpu_threads: int = 8
    asr_vad: bool = True
    padding: float = 6.0
    snap_tolerance: float = 3.0
    light_correct: bool = True


@dataclass
class EditStyleConfig:
    font_family: str = "Noto Sans SC"
    font_size: int = 20
    margin_v: int = 60


@dataclass
class UploadConfig:
    auto_start: bool = True
    poll_interval: int = 10
    max_attempts: int = 3
    retry_base: int = 30
    auth_retry: int = 120
    delete_after_success: bool = True


@dataclass
class PathsConfig:
    videos_dir: Path = field(default_factory=lambda: Path("Videos"))
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    db_path: Path | None = None
    cookie_file: Path | None = None
    config_path: Path | None = None


@dataclass
class Settings:
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    burst: BurstConfig = field(default_factory=BurstConfig)
    mimo: MimoConfig = field(default_factory=MimoConfig)
    quality: QualityGate = field(default_factory=QualityGate)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    subtitle: SubtitleConfig = field(default_factory=SubtitleConfig)
    edit: EditStyleConfig = field(default_factory=EditStyleConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)

    def videos_root(self) -> Path:
        return self.paths.videos_dir.resolve()

    def ensure_within_videos(self, path: Path) -> Path:
        """解析路径并限制在 videos 根内，禁止穿越。"""
        root = self.videos_root()
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"path escapes videos root: {path}")
        return resolved


def _apply_table(obj: Any, data: dict[str, Any]) -> None:
    for key, value in data.items():
        if not hasattr(obj, key):
            continue
        current = getattr(obj, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _apply_table(current, value)
        elif isinstance(current, Path) or key.endswith("_path") or key.endswith("_file"):
            setattr(obj, key, Path(value) if value not in (None, "") else None)
        else:
            setattr(obj, key, value)


def load_settings(config_path: Path | None = None) -> Settings:
    settings = Settings()
    path = config_path or (
        Path(os.environ["BILIVE_CONFIG"])
        if os.environ.get("BILIVE_CONFIG")
        else Path(DEFAULT_CONFIG_NAME)
    )
    settings.paths.config_path = path

    if path.is_file():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        for section in (
            "worker",
            "video",
            "burst",
            "mimo",
            "quality",
            "analysis",
            "subtitle",
            "edit",
            "upload",
        ):
            table_key = section
            if section == "burst":
                table_key = "slice"
            data = raw.get(table_key)
            if not isinstance(data, dict):
                continue
            if section == "burst":
                burst = data.get("burst")
                if isinstance(burst, dict):
                    _apply_table(settings.burst, burst)
                mimo = data.get("mimo")
                if isinstance(mimo, dict):
                    _apply_table(settings.mimo, mimo)
                quality = data.get("quality")
                if isinstance(quality, dict):
                    _apply_table(settings.quality, quality)
                analysis = data.get("analysis")
                if isinstance(analysis, dict):
                    _apply_table(settings.analysis, analysis)
                subtitle = data.get("subtitle")
                if isinstance(subtitle, dict):
                    _apply_table(settings.subtitle, subtitle)
                edit = data.get("edit")
                if isinstance(edit, dict):
                    _apply_table(settings.edit, edit)
            else:
                _apply_table(getattr(settings, section), data)

    env_videos = os.environ.get("BILIVE_VIDEOS_DIR")
    if env_videos:
        settings.paths.videos_dir = Path(env_videos)
    env_log = os.environ.get("BILIVE_LOG_DIR")
    if env_log:
        settings.paths.log_dir = Path(env_log)
    env_db = os.environ.get("BILIVE_DB_PATH")
    if env_db:
        settings.paths.db_path = Path(env_db)
    env_cookie = os.environ.get("BILIVE_COOKIE_FILE")
    if env_cookie:
        settings.paths.cookie_file = Path(env_cookie)

    env_idle = os.environ.get("BILIVE_WORKER_IDLE_TIMEOUT")
    if env_idle:
        settings.worker.idle_timeout = int(env_idle)
    env_idle_check = os.environ.get("BILIVE_WORKER_IDLE_CHECK_INTERVAL")
    if env_idle_check:
        settings.worker.idle_check_interval = int(env_idle_check)

    return settings
