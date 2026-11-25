"""Encode candidate videos, package Batch requests, and collect MiMo responses."""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Iterable

from openai import OpenAI

from bilive.config.secrets import redact
from bilive.vendor.autoslice.mllm_sdk.mimo_video import (
    MimoClipResults, _analysis_list_from_mimo_dict, _extract_json,
    candidate_request_body, encode_video_for_mimo,
)

BATCH_BASE_URL = "https://batch-api-cn.xiaomimimo.com/v1"
ENDPOINT = "/v1/chat/completions"
# Application budgets. The file service rejected larger video rows even though
# they fit its documented 128 MB file limit; local probes accepted a 16 MB row.
MAX_LINE_BYTES = 12_000_000
MAX_FILE_BYTES = 16_000_000


class BatchCredentialsError(ValueError):
    """Local Batch credentials are missing or unsuitable."""


def _jsonl(row: dict) -> bytes:
    return (json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def build_batch_line(custom_id: str, mimo, *, video_path, max_line_bytes=MAX_LINE_BYTES, **judge_kwargs) -> bytes:
    """Keep the entire candidate and size its analysis copy for one JSONL row."""
    if mimo.model not in {"mimo-v2.6-flash", "mimo-v2.6-pro"}:
        raise ValueError(f"当前模型不支持 MiMo Batch：{mimo.model}")
    if not custom_id:
        raise ValueError("批量请求缺少候选标识")
    duration = float(judge_kwargs["candidate_duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("候选时长无效")
    prefix = "data:video/mp4;base64,"
    body = candidate_request_body(prefix, model=mimo.model, fps=mimo.fps,
                                  media_resolution=mimo.media_resolution, **judge_kwargs)
    row = {"custom_id": custom_id, "method": "POST", "url": ENDPOINT, "body": body}
    budget = min(mimo.max_base64_bytes, max_line_bytes - len(_jsonl(row)))
    # Reserve space for MP4 muxing and rate-control variation before Base64.
    total_kbps = int(budget * .75 * 8 * .91 / duration / 1000)
    if total_kbps < 72:
        raise ValueError("候选过长或文字过多，当前批量输入预算不足以保留完整音画")
    audio_kbps = min(64, max(32, total_kbps // 5))
    video_kbps = min(900, total_kbps - audio_kbps)
    bitrates = ((f"{video_kbps}k", f"{audio_kbps}k"),
                (f"{int(video_kbps * .55)}k", f"{audio_kbps}k"))
    encoded = encode_video_for_mimo(video_path, max_base64_bytes=budget, bitrates=bitrates,
                                    frame_rate=max(4.0, float(mimo.fps) * 2))
    body["messages"][1]["content"][0]["video_url"]["url"] = encoded.url
    line = _jsonl(row)
    if len(line) > max_line_bytes:
        raise ValueError("候选请求超过单行预算")
    return line


def write_batch_parts(directory: Path, lines: Iterable[tuple[str, bytes]], *,
                      max_file_bytes: int = MAX_FILE_BYTES) -> list[tuple[Path, list[str]]]:
    """Return closed files and their IDs. A failed preparation leaves no partial parts."""
    directory.mkdir(parents=True, exist_ok=True)
    parts = []
    seen = set()
    handle = None
    try:
        for cid, line in lines:
            if cid in seen or not cid:
                raise ValueError("批量输入含空白或重复的候选标识")
            if len(line) > max_file_bytes or not line.endswith(b"\n"):
                raise ValueError("无法将候选写入批量 JSONL 文件")
            if handle is None or handle.tell() + len(line) > max_file_bytes:
                if handle is not None:
                    handle.close()
                handle = tempfile.NamedTemporaryFile(dir=directory, prefix="batch-input-", suffix=".jsonl", delete=False)
                parts.append((Path(handle.name), []))
            handle.write(line)
            parts[-1][1].append(cid)
            seen.add(cid)
        if handle is not None:
            handle.close()
        return parts
    except BaseException:
        if handle is not None:
            handle.close()
        for path, _ in parts:
            path.unlink(missing_ok=True)
        raise


class MimoBatchClient:
    def __init__(self, client=None):
        if client is None:
            key = os.environ.get("MIMO_API_KEY", "")
            if not key.startswith("sk-"):
                raise BatchCredentialsError("MiMo 批量推理需要按量付费的普通 API Key")
            client = OpenAI(api_key=key, base_url=BATCH_BASE_URL, timeout=180, max_retries=0)
        self.client = client

    def upload(self, path: Path) -> str:
        with path.open("rb") as data:
            return self.client.files.create(file=data, purpose="batch").id

    def create(self, file_id: str, name: str) -> str:
        return self.client.batches.create(input_file_id=file_id, endpoint=ENDPOINT,
                                          completion_window="24h", extra_body={"name": name}).id

    def find(self, file_id: str) -> str | None:
        # Iteration over the SDK cursor page also traverses subsequent pages.
        found = [batch.id for batch in self.client.batches.list(limit=100) if batch.input_file_id == file_id]
        if len(found) > 1:
            raise ValueError("同一输入文件已有多个远端批次，请核对平台记录")
        return found[0] if found else None

    def status(self, batch_id: str) -> dict:
        return self.client.batches.retrieve(batch_id).model_dump()

    def results(self, status: dict, expected_ids: Iterable[str]) -> dict[str, dict]:
        expected = set(expected_ids)
        counts = status.get("request_counts") or {}
        phase = str(status["status"]).lower()
        if phase == "completed" and expected and not (status.get("output_file_id") or status.get("error_file_id")):
            raise RuntimeError("MiMo 批次已结束，结果文件仍在生成")
        rows = {}
        for field, count in (("output_file_id", "completed"), ("error_file_id", "failed")):
            file_id = status.get(field)
            if not file_id:
                if (counts.get(count) or 0) > 0:
                    raise RuntimeError("MiMo 批次已结束，结果文件仍在生成")
                continue
            try:
                content = self.client.files.content(file_id)
            except Exception as exc:
                # A completed job can expose a file ID before that file is downloadable.
                if getattr(exc, "status_code", None) in (404, 409, 425):
                    raise RuntimeError("MiMo 批量结果文件尚未就绪，请稍后获取") from exc
                raise
            for line in content.iter_lines():
                if not line.strip():
                    continue
                row = json.loads(line)
                cid = row.get("custom_id") if isinstance(row, dict) else None
                if not isinstance(cid, str) or cid not in expected or cid in rows:
                    raise ValueError("批量结果的候选标识未知或重复")
                rows[cid] = row
        return rows


def parse_batch_result(row: dict, *, artist: str, model: str, duration: float) -> MimoClipResults:
    """Interpret one successfully matched result; errors stay distinct from empty selections."""
    response = row.get("response")
    if not isinstance(response, dict):
        response = {}
    body = response.get("body")
    if not isinstance(body, dict):
        body = {}
    if row.get("error") or response.get("status_code") != 200:
        error = row.get("error") or body.get("error")
        reason = (error.get("message") or error.get("code")) if isinstance(error, dict) else error
        raise ValueError("MiMo 批量判断失败：" + redact(str(reason or response.get("status_code")))[:400])
    if body.get("model") != model:
        raise ValueError("批量结果模型与请求不符")
    choices = body.get("choices")
    if (not isinstance(choices, list) or not choices or not isinstance(choices[0], dict)
            or choices[0].get("finish_reason") != "stop"):
        raise ValueError("批量判断未正常完成")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    parsed = _extract_json(content) if isinstance(content, str) else None
    if not parsed or not isinstance(parsed.get("clips"), list):
        raise ValueError("批量判断缺少有效的 clips 数组")
    results = _analysis_list_from_mimo_dict(parsed, artist=artist, model=model)
    usage = body.get("usage") or {}
    for result in results:
        if result.judge_status == "judge_failed":
            raise ValueError(result.judge_error or "批量判断格式无效")
        trim = result.suggested_trim
        if trim and not 0 <= trim.trim_start < trim.trim_end <= duration + .01:
            raise ValueError("批量判断选段超出候选范围")
        result.token_usage = usage
    results.token_usage = usage
    return results
