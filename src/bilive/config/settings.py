"""配置解析：环境变量 > 配置文件 > 默认值。"""

from __future__ import annotations

import os
import json
import re
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
    encoder: str = "libx264"


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
    base_url: str = "https://api.xiaomimimo.com/v1"
    model: str = "mimo-v2.6-flash"
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
    auto_start: bool = False
    pre_judge_asr: bool = True
    asr_prompt_max_chars: int = 4000


@dataclass
class SubtitleConfig:
    asr_model: str = "large-v3"
    asr_device: str = "cuda"
    asr_compute_type: str = "float16"
    asr_batch: int = 2
    asr_cpu_threads: int = 2
    asr_vad: bool = True
    padding: float = 6.0
    snap_tolerance: float = 3.0
    snap_enabled: bool = True
    light_correct: bool = True


@dataclass
class EditStyleConfig:
    font_family: str = "Noto Sans SC"
    font_size: int = 20
    margin_v: int = 60
    alignment: int | None = None
    outline: float | None = None
    primary_colour: str | None = None
    outline_colour: str | None = None


@dataclass
class MultiModalConfig:
    whisper_engine: str = 'faster-whisper'
    whisper_vad_min_silence_ms: int = 2000
    whisper_vad_speech_pad_ms: int = 400
    unload_audio_model_after_analysis: bool = True


@dataclass
class DashboardConfig:
    allowed_hosts: list[str] = field(default_factory=list)


@dataclass
class UploadConfig:
    upload_line: str = 'auto'
    tid: int = 138
    auto_start: bool = True
    poll_interval: int = 10
    max_attempts: int = 3
    retry_base: int = 30
    auth_retry: int = 120
    delete_after_success: bool = True


@dataclass
class PublicationConfig:
    title_template: str = "{title}"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    tid: int | None = None


@dataclass
class RetentionConfig:
    enabled: bool = True
    unprocessed_days: int = 5
    published_hours: int = 24


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
    multi_modal: MultiModalConfig = field(default_factory=MultiModalConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    publication: PublicationConfig = field(default_factory=PublicationConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)

    def subtitle_style(self) -> dict:
        return {'font_name':self.edit.font_family,**{k:v for k,v in vars(self.edit).items() if k != 'font_family' and v is not None}}

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
            "dashboard",
            "publication",
            "retention",
            "multi_modal",
            "paths",
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
                _apply_table(settings.multi_modal,data.get('multi_modal',{}))
            else:
                _apply_table(getattr(settings, section), data)

        # Keep the pinned configuration domains and keys usable. Explicit newer
        # subtitle/edit keys take priority when both forms are present.
        sliced=raw.get('slice',{}); sub={**sliced.get('subtitle',{}), **raw.get('subtitle',{})}; edit={**sliced.get('edit',{}), **raw.get('edit',{})}
        multi={**sliced.get('multi_modal',{}), **raw.get('multi_modal',{})}; analysis={**sliced.get('analysis',{}), **raw.get('analysis',{})}
        for key in ('upload_line','tid'):
            if key in raw.get('video',{}) and key not in raw.get('upload',{}):
                setattr(settings.upload,key,raw['video'][key])
        for old,new in {'whisper_model':'asr_model','whisper_device':'asr_device','whisper_compute_type':'asr_compute_type',
                        'whisper_batch_size':'asr_batch','whisper_cpu_threads':'asr_cpu_threads','whisper_vad_filter':'asr_vad'}.items():
            if old in multi and new not in sub:setattr(settings.subtitle,new,multi[old])
        if 'enable_subtitle_correct' in sub and 'light_correct' not in sub:
            settings.subtitle.light_correct=sub['enable_subtitle_correct']
        for old,new in {'trim_asr_padding_seconds':'padding','snap_trim_tolerance':'snap_tolerance'}.items():
            if old in analysis and new not in sub:setattr(settings.subtitle,new,analysis[old])
        if 'snap_trim_to_segments' in analysis and 'snap_enabled' not in sub:
            settings.subtitle.snap_enabled=analysis['snap_trim_to_segments']
        if 'pre_judge_asr_max_chars' in analysis and 'asr_prompt_max_chars' not in analysis:
            settings.analysis.asr_prompt_max_chars=analysis['pre_judge_asr_max_chars']
        for old,new in {'font_name':'font_family','font_size':'font_size','margin_v':'margin_v','alignment':'alignment',
                        'outline':'outline','primary_colour':'primary_colour','outline_colour':'outline_colour'}.items():
            if old in sub and new not in edit:setattr(settings.edit,new,sub[old])

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

    if 'BILIVE_DASHBOARD_ALLOWED_HOSTS' in os.environ:
        settings.dashboard.allowed_hosts=[v.strip().lower() for v in os.environ['BILIVE_DASHBOARD_ALLOWED_HOSTS'].split(',') if v.strip()]
    for env,key,cast in [('BILIVE_WHISPER_BATCH_SIZE','asr_batch',int),('BILIVE_WHISPER_CPU_THREADS','asr_cpu_threads',int),
                         ('BILIVE_WHISPER_VAD_FILTER','asr_vad',lambda v:v.lower() in {'1','true','yes','on'}),
                         ('BILIVE_TRIM_ASR_PADDING_SECONDS','padding',float),('BILIVE_SNAP_TRIM_TOLERANCE','snap_tolerance',float),
                         ('BILIVE_SNAP_TRIM','snap_enabled',lambda v:v.lower() in {'1','true','yes','on'}),
                         ('BILIVE_ENABLE_SUBTITLE_CORRECT','light_correct',lambda v:v.lower() in {'1','true','yes','on'})]:
        if env in os.environ:setattr(settings.subtitle,key,cast(os.environ[env]))
    for env,key in [('BILIVE_WHISPER_VAD_MIN_SILENCE_MS','whisper_vad_min_silence_ms'),('BILIVE_WHISPER_VAD_SPEECH_PAD_MS','whisper_vad_speech_pad_ms')]:
        if env in os.environ:setattr(settings.multi_modal,key,int(os.environ[env]))
    if 'BILIVE_PRE_JUDGE_ASR_MAX_CHARS' in os.environ:
        settings.analysis.asr_prompt_max_chars=int(os.environ['BILIVE_PRE_JUDGE_ASR_MAX_CHARS'])

    if "BILIVE_VIDEO_ENCODER" in os.environ:
        settings.video.encoder = os.environ["BILIVE_VIDEO_ENCODER"]
    if settings.video.encoder not in {"libx264", "h264_nvenc"}:
        raise ValueError("video.encoder must be libx264 or h264_nvenc")
    return settings


def save_analysis_auto_start(config_path: Path, enabled: bool) -> bool:
    if type(enabled) is not bool:
        raise ValueError('auto_start must be a boolean')
    text = config_path.read_text(encoding='utf-8-sig') if config_path.is_file() else ''
    tomllib.loads(text)
    # The top-level analysis table overrides legacy slice.analysis values.
    match = re.search(r'^\s*\[analysis\]\s*(?:#.*)?$', text, re.MULTILINE)
    assignment = f'auto_start = {str(enabled).lower()}\n'
    if match:
        following = re.search(r'^\s*\[', text[match.end():], re.MULTILINE)
        end = match.end()+following.start() if following else len(text)
        section = re.sub(r'^\s*auto_start\s*=.*(?:\n|$)', '', text[match.end():end], flags=re.MULTILINE)
        updated = text[:match.end()]+'\n'+section.strip()+'\n'+assignment+'\n'+text[end:]
    else:
        updated = text.rstrip()+'\n\n[analysis]\n'+assignment
    tomllib.loads(updated)
    temp = config_path.with_suffix(config_path.suffix+'.tmp')
    temp.write_text(updated, encoding='utf-8')
    temp.replace(config_path)
    return load_settings(config_path).analysis.auto_start


def save_subtitle_style(config_path: Path, style: dict) -> dict:
    """Update only the appearance table, preserving other configuration text."""
    from bilive.vendor.subtitle_style import validate_style, SubtitleStyle
    validate_style(style)
    normalized = SubtitleStyle.from_mapping(style).to_mapping()
    values = {'font_family': normalized.pop('font_name'), **normalized}
    text = config_path.read_text(encoding='utf-8-sig') if config_path.is_file() else ''
    # Refuse to overwrite an invalid configuration file.
    tomllib.loads(text)
    match = re.search(r'^\s*\[edit\]\s*(?:#.*)?$', text, re.MULTILINE)
    end = None
    if match:
        following = re.search(r'^\s*\[', text[match.end():], re.MULTILINE)
        end = match.end()+following.start() if following else len(text)
        section = text[match.end():end]
        keys = '|'.join(re.escape(k) for k in EditStyleConfig.__dataclass_fields__)
        section = re.sub(rf'^\s*(?:{keys})\s*=.*(?:\n|$)', '', section, flags=re.MULTILINE)
    else:
        section = '\n'
    assignments = '\n'.join(f'{key} = {json.dumps(value, ensure_ascii=False)}' for key, value in values.items())+'\n'
    section = '\n'+section.strip()+'\n' if section.strip() else '\n'
    if match:
        updated = text[:match.end()]+section+assignments+'\n'+text[end:]
    else:
        updated = text.rstrip()+'\n\n[edit]\n'+assignments
    tomllib.loads(updated)
    temp = config_path.with_suffix(config_path.suffix+'.tmp')
    temp.write_text(updated, encoding='utf-8')
    temp.replace(config_path)
    return load_settings(config_path).subtitle_style()
