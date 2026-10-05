"""Validated, atomic settings edits; secrets and service identity are excluded."""
from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit

from bilive.config.settings import Settings, load_settings

# Bounds describe the actual configurable domains, not arbitrary TOML writes.
BOUNDS = {
    'worker': {'idle_timeout': (1, 86400), 'idle_check_interval': (1, 3600)},
    'video': {'min_source_size_mb': (0, 100000)},
    'burst': {'ratio': (1, 100), 'window': (1, 3600), 'context': (1, 7200),
              'merge_gap': (0, 3600), 'top_n': (1, 100), 'lag': (-3600, 3600)},
    'mimo': {'fps': (1, 60), 'timeout': (1, 3600), 'max_base64_bytes': (1024, 100000000),
             'request_concurrency': (1, 32), 'encode_concurrency': (1, 16)},
    'quality': {key: (0, 1) for key in ('quality', 'completeness', 'confidence')},
    'analysis': {'asr_prompt_max_chars': (1, 100000)},
    'subtitle': {'asr_batch': (1, 128), 'asr_cpu_threads': (1, 128),
                 'padding': (0, 120), 'snap_tolerance': (0, 120)},
    'multi_modal': {'whisper_vad_min_silence_ms': (0, 60000), 'whisper_vad_speech_pad_ms': (0, 10000)},
    'upload': {'tid': (1, 10000), 'poll_interval': (1, 3600), 'max_attempts': (1, 100),
               'retry_base': (1, 86400), 'auth_retry': (1, 86400)},
    'publication': {'tid': (1, 10000)},
    'retention': {'unprocessed_days': (1, 3650), 'published_hours': (1, 87600)},
}
ENUMS = {
    'subtitle.asr_device': ['cuda', 'cpu', 'auto'],
    'subtitle.asr_compute_type': ['float16', 'float32', 'int8', 'int8_float16', 'int8_float32', 'auto', 'default'],
    'multi_modal.whisper_engine': ['faster-whisper'],
}
SECTIONS = [*BOUNDS, 'paths', 'edit']


def editable_values(settings: Settings) -> dict:
    data = asdict(settings)
    result = {key: data[key] for key in SECTIONS}
    result['paths'] = {key: str(data['paths'][key] or '') for key in ('videos_dir', 'log_dir', 'db_path')}
    return result


def schema() -> dict:
    values = editable_values(Settings())
    return {section: {key: {'default': value, 'bounds': BOUNDS.get(section, {}).get(key),
                          'choices': ENUMS.get(f'{section}.{key}'),
                          'effect': 'restart' if section not in {'publication', 'retention', 'edit'} and
                          (section, key) != ('analysis', 'auto_start') else 'immediate'}
                       for key, value in fields.items()} for section, fields in values.items()}


def validate_changes(changes: dict) -> dict:
    if not isinstance(changes, dict) or not changes:
        raise ValueError('没有设置修改')
    defaults = editable_values(Settings())
    for section, fields in changes.items():
        if section not in defaults or not isinstance(fields, dict) or not fields:
            raise ValueError(f'无效设置分区: {section}')
        if section == 'edit':
            from bilive.vendor.subtitle_style import validate_style
            if set(fields) - set(defaults[section]):
                raise ValueError('不可编辑的字幕设置')
            for key, value in fields.items():
                if value is None and defaults[section][key] is None:
                    continue
                expected = (int if key in {'font_size', 'margin_v', 'alignment'} else
                            float if key == 'outline' else str)
                if not (type(value) in (int, float) if expected is float else type(value) is expected):
                    raise ValueError(f'设置类型无效: edit.{key}')
            validate_style({'font_name' if k == 'font_family' else k: v
                            for k, v in fields.items() if v is not None})
            continue
        for key, value in fields.items():
            if key not in defaults[section]:
                raise ValueError(f'不可编辑的设置: {section}.{key}')
            original = defaults[section][key]
            if section == 'publication' and key == 'tid' and value is None:
                continue
            expected = int if original is None else type(original)
            valid = (type(value) in (int, float) if expected is float else type(value) is expected)
            if not valid:
                raise ValueError(f'设置类型无效: {section}.{key}')
            bounds = BOUNDS.get(section, {}).get(key)
            if bounds and (not math.isfinite(value) or not bounds[0] <= value <= bounds[1]):
                raise ValueError(f'{section}.{key} 必须在 {bounds[0]}–{bounds[1]} 之间')
            choices = ENUMS.get(f'{section}.{key}')
            if choices and value not in choices:
                raise ValueError(f'{section}.{key} 取值无效')
            if isinstance(value, str) and (len(value) > 2000 or '\x00' in value):
                raise ValueError(f'{section}.{key} 过长或包含无效字符')
            if isinstance(original, str) and section not in {'publication', 'paths'} and not value.strip():
                raise ValueError(f'{section}.{key} 不能为空')
            if section == 'paths' and key != 'db_path' and not value.strip():
                raise ValueError(f'{section}.{key} 不能为空')
            if section == 'mimo' and key == 'base_url':
                url = urlsplit(value)
                if url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password:
                    raise ValueError('AI 服务地址须为无凭据的 HTTP(S) 地址')
    if 'publication' in changes:
        from bilive.media.publication import validate_template, validate_metadata
        fields = changes['publication']
        validate_template(fields.get('title_template', '{title}'))
        validate_metadata('标题', fields.get('description', ''), fields.get('tags', []), fields.get('tid'))
    return changes


def save_changes(path: Path, changes: dict) -> dict:
    validate_changes(changes)
    text = path.read_text(encoding='utf-8-sig') if path.is_file() else ''
    tomllib.loads(text)
    # Canonical top-level sections override pinned slice.* tables on reload.
    for section, fields in changes.items():
        match = re.search(rf'^\[{section}\][ \t]*(?:#.*)?$', text, re.MULTILINE)
        end = len(text)
        if match:
            following = re.search(r'^\[', text[match.end():], re.MULTILINE)
            end = match.end() + following.start() if following else end
            body = text[match.end():end]
        else:
            body = '\n'
        # Parse each table first; only scalar or single-line array assignments
        # are produced here. Existing multiline values are replaced in full.
        for key, value in fields.items():
            lines = body.splitlines(keepends=True)
            kept = []
            index = 0
            while index < len(lines):
                line = lines[index]
                if re.match(rf'^[ \t]*{re.escape(key)}[ \t]*=', line):
                    assignment = line
                    while True:
                        try:
                            tomllib.loads(assignment)
                            break
                        except tomllib.TOMLDecodeError:
                            index += 1
                            if index >= len(lines):
                                raise ValueError('配置字段不能安全替换')
                            assignment += lines[index]
                else:
                    kept.append(line)
                index += 1
            body = ''.join(kept)
            if value is not None:
                body = body.rstrip() + f'\n{key} = {json.dumps(value, ensure_ascii=False)}\n'
        if match:
            text = text[:match.end()] + body + '\n' + text[end:]
        else:
            text = text.rstrip() + f'\n\n[{section}]' + body
    tomllib.loads(text)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(path)
    effective = editable_values(load_settings(path))
    restart = [f'{s}.{k}' for s, fields in changes.items() for k in fields
               if schema()[s][k]['effect'] == 'restart']
    overrides = [f'{s}.{k}' for s, fields in changes.items() for k, v in fields.items()
                 if effective[s][k] != v]
    return {'values': effective, 'restart_required': restart, 'environment_overrides': overrides}
