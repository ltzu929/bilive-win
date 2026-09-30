from pathlib import Path

import pytest

from bilive.db.conn import DatabaseUnavailable, init_upload_db
from bilive.upload.queue import UploadQueue


@pytest.fixture
def queue(tmp_path: Path):
    db = tmp_path / "upload.db"
    init_upload_db(db)
    return UploadQueue(db)


def test_missing_db_is_unavailable(tmp_path: Path):
    q = UploadQueue(tmp_path / "missing.db")
    with pytest.raises(DatabaseUnavailable):
        q.list()


def test_staged_is_invisible_in_default_list(queue: UploadQueue):
    queue.stage("/v/a.mp4", title="A")
    listing = queue.list()
    assert listing["total"] == 0
    assert queue.get_by_path("/v/a.mp4")["stage"] == "staged"


def test_activate_moves_staged_to_queued(queue: UploadQueue):
    queue.stage("/v/a.mp4")
    row = queue.activate("/v/a.mp4")
    assert row["stage"] == "queued"


def test_activate_with_remote_goes_to_uploaded(queue: UploadQueue):
    queue.stage("/v/b.mp4")
    # 模拟已上传过的远端标识场景：直接写 remote 后再激活
    import sqlite3

    conn = sqlite3.connect(queue.db_path)
    conn.execute(
        "UPDATE upload_queue SET remote_filename=? WHERE video_path=?",
        ("remote-b.mp4", "/v/b.mp4"),
    )
    conn.commit()
    conn.close()
    row = queue.activate("/v/b.mp4")
    assert row["stage"] == "uploaded"


def test_manual_retry_only_failed_without_remote(queue: UploadQueue):
    queue.stage("/v/c.mp4")
    queue.activate("/v/c.mp4")
    queue.mark_failed("/v/c.mp4", "boom")
    row = queue.manual_retry("/v/c.mp4")
    assert row["stage"] == "queued"

    queue.stage("/v/d.mp4")
    queue.activate("/v/d.mp4")
    queue.mark_uploaded("/v/d.mp4", "remote-d.mp4")
    queue.mark_failed("/v/d.mp4", "publish fail", has_remote=True)
    with pytest.raises(ValueError):
        queue.manual_retry("/v/d.mp4")


def test_published_requires_bvid(queue: UploadQueue):
    queue.stage("/v/e.mp4")
    queue.activate("/v/e.mp4")
    queue.mark_uploaded("/v/e.mp4", "remote-e.mp4")
    with pytest.raises(ValueError):
        queue.mark_published("/v/e.mp4", "")
    row = queue.mark_published("/v/e.mp4", "BV1xx")
    assert row["stage"] == "published" and row["bvid"] == "BV1xx"


def test_claim_skips_staged_and_respects_attempts(queue: UploadQueue):
    queue.stage("/v/f.mp4")
    assert queue.claim_next() is None  # staged 不可领取

    queue.stage("/v/g.mp4")
    queue.activate("/v/g.mp4")
    claimed = queue.claim_next()
    assert claimed is not None and claimed["video_path"] == "/v/g.mp4"
    assert queue.claim_next() is None  # uploading 不再被领


def test_withdraw_rejects_published(queue: UploadQueue):
    queue.stage("/v/h.mp4")
    queue.activate("/v/h.mp4")
    queue.mark_uploaded("/v/h.mp4", "remote-h.mp4")
    queue.mark_published("/v/h.mp4", "BV1h")
    with pytest.raises(ValueError):
        queue.withdraw("/v/h.mp4")
