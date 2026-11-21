"""Generate one isolated MOSS cover candidate; never update a clip or upload queue."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import struct
import sys
import time
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bilive.config.secrets import load_secret_env, redact

MODELS = ('moss-image-base-1.0', 'moss-image-sft-1.0')
DEFAULT_PROMPT = json.dumps({
    'summary': {
        'meta': ['poster'], 'canvas': 'single',
        'caption': 'A minimal 16:9 livestream highlight thumbnail. Flat vector style, dark navy background, '
                   'bright yellow and white, generous clean negative space. Exactly two lines of large '
                   'Chinese headline on the right, three simple empty speech bubbles on the left. '
                   'Render ONLY the text in the text items. No subtitle, no footer, no additional words, '
                   'no UI, no avatars, no faces, no decorations containing text. All speech bubbles are '
                   'completely empty except for three round dots.',
    },
    'items': [
        {'label': 'text', 'text': '弹幕太会', 'bbox': [-260, -40, -50, 660],
         'caption': 'Very large white bold Chinese headline, exact characters, horizontal left to right.'},
        {'label': 'text', 'text': '接梗了', 'bbox': [-10, -40, 230, 660],
         'caption': 'Very large bright yellow bold Chinese headline, exact characters, horizontal left to right.'},
        {'label': 'object', 'bbox': [-260, -680, 250, -170],
         'caption': 'Three large overlapping flat vector chat bubbles in white and yellow. Each bubble '
                    'contains only three round dots. No letters, no Chinese characters, no numbers. '
                    'Simple playful geometric illustration.'},
    ],
    'bbox_coord_space': 'abs_centralized', 'bbox_format': 'yxyx',
}, ensure_ascii=False)


def build_request(prompt: str, model: str, seed: int) -> dict:
    if not prompt.strip() or len(prompt) > 4000:
        raise ValueError('提示词必须为 1–4000 个字符')
    if model not in MODELS:
        raise ValueError('不支持的图像模型')
    if not 0 <= seed <= 2**53 - 1:
        raise ValueError('seed 必须在 0–2^53−1 之间')
    return {
        'model': model, 'prompt': prompt, 'size': '1536x864', 'n': 1,
        'seed': seed, 'response_format': 'url',
        'num_inference_steps': 50, 'guidance_scale': 3.0,
        'negative_prompt': 'nsfw, worst quality, bad anatomy, jpeg artifact',
        'output_watermark': {'enabled': True},
    }


def generate(request: dict, output: Path, *, api_key: str, timeout: float = 900) -> dict:
    if not api_key.strip():
        raise ValueError('未配置 MOSS_API_KEY，请添加到 .secrets/env')
    # A new directory preserves earlier candidates and prevents accidental replacement.
    output.mkdir(parents=True, exist_ok=False)
    (output / 'request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
    started = time.monotonic()
    # No automatic retry: a timed-out synchronous request may already have generated an image.
    response = requests.post(
        'https://api.mosi.cn/v1/images/generations',
        headers={'Authorization': f'Bearer {api_key}'}, json=request, timeout=timeout,
    )
    if response.status_code != 200:
        raise ValueError(f'MOSS 生成失败，HTTP {response.status_code}；未自动重试')
    payload = response.json()
    rows = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError('MOSS 返回缺少单张图片结果')
    url = rows[0].get('url')
    if not isinstance(url, str) or urlsplit(url).scheme != 'https' or not urlsplit(url).netloc:
        raise ValueError('MOSS 未返回有效的 HTTPS 图片地址')
    (output / 'response.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    # Download separately so the MOSS API key is never sent to the asset host.
    image = requests.get(url, timeout=60)
    image.raise_for_status()
    content = image.content
    if (len(content) < 45 or content[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
            or content[-12:] != b'\x00\x00\x00\x00IEND\xaeB`\x82'):
        raise ValueError('图片下载结果不是完整的 PNG 容器')
    width, height = struct.unpack('>II', content[16:24])
    if (width, height) != (1536, 864):
        raise ValueError(f'图片尺寸与请求不符：{width}x{height}')
    (output / 'cover.png').write_bytes(content)
    result = {
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'model': request['model'], 'elapsed_seconds': round(time.monotonic() - started, 2),
        'width': width, 'height': height, 'usage': payload.get('usage'),
        'image': str((output / 'cover.png').resolve()),
        'visual_review': 'pending', 'production_applied': False,
    }
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=MODELS, default=MODELS[1])
    parser.add_argument('--prompt-file', type=Path, help='UTF-8 text, or serialized layout JSON')
    parser.add_argument('--seed', type=int, default=20261024)
    parser.add_argument('--output', type=Path, help='New directory for isolated artifacts')
    parser.add_argument('--dry-run', action='store_true', help='Print the request without network or credentials')
    args = parser.parse_args(argv)
    load_secret_env(ROOT / '.secrets/env')
    try:
        prompt = args.prompt_file.read_text(encoding='utf-8-sig') if args.prompt_file else DEFAULT_PROMPT
        request = build_request(prompt, args.model, args.seed)
        if args.dry_run:
            print(json.dumps(request, ensure_ascii=False, indent=2))
            return 0
        output = args.output or ROOT / 'artifacts/research/moss-cover' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        result = generate(request, output, api_key=os.environ.get('MOSS_API_KEY', ''))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, requests.RequestException) as exc:
        print(redact(f'封面试验失败：{exc}'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
