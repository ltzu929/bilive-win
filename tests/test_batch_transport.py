import base64
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from bilive.analysis import batch_transport as batch
from bilive.config.settings import MimoConfig


def test_subscription_key_disables_batch_without_a_network_request(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "tp-test")
    with pytest.raises(batch.BatchCredentialsError):
        batch.MimoBatchClient()


def test_request_contains_decodable_video_audio_and_realtime_prompt(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg required")
    monkeypatch.setenv("BILIVE_VIDEO_ENCODER", "libx264")
    source = tmp_path / "candidate.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=12",
                    "-f", "lavfi", "-i", "sine=frequency=600", "-t", "1.5",
                    "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
    line = batch.build_batch_line("w-1", MimoConfig(), video_path=source, artist="测试主播",
                                  candidate_duration=1.5, danmaku_text="你好", guidance="保留回应",
                                  candidate_transcript="0.30–1.00s 欢迎", max_line_bytes=200_000)
    assert len(line) <= 200_000 and line.endswith(b"\n")
    row = json.loads(line)
    assert row["custom_id"] == "w-1" and row["url"] == batch.ENDPOINT
    body = row["body"]
    assert body["max_completion_tokens"] == 2048 and body["thinking"] == {"type": "disabled"}
    video, prompt = body["messages"][1]["content"]
    assert "保留回应" in prompt["text"] and "0.30–1.00s 欢迎" in prompt["text"]
    assert video["fps"] == 1 and video["media_resolution"] == "default"
    encoded = tmp_path / "batch.mp4"
    prefix, raw = video["video_url"]["url"].split(",", 1)
    assert prefix == "data:video/mp4;base64"
    encoded.write_bytes(base64.b64decode(raw, validate=True))
    info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams",
                                               "-of", "json", str(encoded)]))
    streams = {s["codec_type"]:s for s in info["streams"]}
    assert streams["video"]["height"] == 720 and streams["audio"]["codec_name"] == "aac"
    assert abs(float(streams["video"]["duration"]) - 1.5) < .30
    assert abs(float(streams["audio"]["duration"]) - 1.5) < .08


def test_parts_count_bytes_and_clean_failed_preparation(tmp_path):
    lines = [("a", b"1234\n"), ("b", b"56\n"), ("c", b"abcdefg\n")]
    parts = batch.write_batch_parts(tmp_path, lines, max_file_bytes=8)
    assert [ids for _, ids in parts] == [["a", "b"], ["c"]]
    assert parts[0][0].read_bytes() == b"1234\n56\n"
    before = set(tmp_path.iterdir())
    with pytest.raises(ValueError, match="重复"):
        batch.write_batch_parts(tmp_path, [*lines, lines[0]], max_file_bytes=8)
    assert set(tmp_path.iterdir()) == before


def _success(cid, clips, **extra):
    return {"custom_id": cid, "response":{"status_code": 200, "body":{
        "model": "mimo-v2.6-flash", "usage":{"total_tokens":321},
        "choices":[{"finish_reason":"stop","message":{"content":json.dumps({"clips":clips, **extra})}}],
    }}}


def test_completed_batch_matches_success_error_and_preserves_usage():
    empty = _success("a", [], empty_reason="等待时长较长")
    failure = {"custom_id":"b", "error":{"message":"media unavailable"}}
    outcome = {"out": [json.dumps(empty)], "err":[json.dumps(failure)]}
    sdk = SimpleNamespace(files=SimpleNamespace(content=lambda key: SimpleNamespace(iter_lines=lambda: outcome[key])))
    transport = batch.MimoBatchClient(sdk)
    status = {"status":"completed", "output_file_id":"out", "error_file_id":"err"}
    rows = transport.results(status, ["b", "a"])
    empty_result = batch.parse_batch_result(rows["a"], artist="某主播", model="mimo-v2.6-flash", duration=240)
    assert empty_result == [] and empty_result.empty_reason == "等待时长较长"
    assert empty_result.token_usage == {"total_tokens":321}
    with pytest.raises(ValueError, match="media unavailable"):
        batch.parse_batch_result(rows["b"], artist="某主播", model="mimo-v2.6-flash", duration=240)
    outcome["err"].append(json.dumps(empty))
    with pytest.raises(ValueError, match="未知或重复"):
        transport.results(status, ["a", "b"])


def test_delayed_file_is_not_an_empty_selection():
    transport = batch.MimoBatchClient(object())
    with pytest.raises(RuntimeError, match="仍在生成"):
        transport.results({"status":"completed","request_counts":{"completed":1}}, ["a"])
    assert transport.results({"status":"expired", "request_counts":{"completed":0}}, ["a"]) == {}
    class Assembling(Exception):
        status_code = 404
    def fetch(file_id):
        raise Assembling(file_id)
    transport = batch.MimoBatchClient(SimpleNamespace(files=SimpleNamespace(content=fetch)))
    with pytest.raises(RuntimeError, match="尚未就绪"):
        transport.results({"status":"completed", "output_file_id":"busy"}, ["a"])


def test_transport_reconciles_create_and_checks_model_and_ranges(tmp_path):
    records = []
    def upload(*, file, purpose):
        assert file.read() == b"hello" and purpose == "batch"
        return SimpleNamespace(id="input-1")
    def create(**kwargs):
        assert kwargs == dict(input_file_id="input-1", endpoint=batch.ENDPOINT,
                              completion_window="24h", extra_body={"name":"recording A"})
        remote = SimpleNamespace(id="batch-1", input_file_id=kwargs["input_file_id"])
        records.append(remote)
        raise TimeoutError("connection lost after create")
    api = batch.MimoBatchClient(SimpleNamespace(files=SimpleNamespace(create=upload),
        batches=SimpleNamespace(create=create,list=lambda limit:records)))
    path = tmp_path / "test.jsonl"
    path.write_bytes(b"hello")
    file_id = api.upload(path)
    with pytest.raises(TimeoutError):
        api.create(file_id, "recording A")
    assert api.find(file_id) == "batch-1"

    valid = {"decision":"keep","reason":"interesting","title":"title","description":"detail",
             "tags":["live"],"quality_score":1.0,"completeness_score":1.0,"confidence":1.0,
             "trim_start":1.5,"trim_end":5.5}
    row = _success("a", [valid])
    kwargs = dict(artist="artist", model="mimo-v2.6-flash", duration=10)
    result = batch.parse_batch_result(row, **kwargs)
    assert len(result) == 1 and result[0].token_usage["total_tokens"] == 321
    with pytest.raises(ValueError, match="超出候选范围"):
        batch.parse_batch_result(_success("a", [{**valid, "trim_end":11}]), **kwargs)
    with pytest.raises(ValueError, match="模型"):
        batch.parse_batch_result(row, **{**kwargs, "model":"mimo-v2.6-pro"})
