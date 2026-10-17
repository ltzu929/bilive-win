"""SQLite 连接与只读语义（规格 §5：缺库返回 unavailable，不隐式建库）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class DatabaseUnavailable(Exception):
    """缺库/缺表时只读查询应抛出并映射为 unavailable。"""


def open_connection(db_path: Path, *, readonly: bool = True) -> sqlite3.Connection:
    if not db_path.is_file():
        raise DatabaseUnavailable(f"database missing: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode={'ro' if readonly else 'rw'}"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def require_table(conn: sqlite3.Connection, table: str) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row is None:
        raise DatabaseUnavailable(f"table missing: {table}")


def init_upload_db(db_path: Path) -> None:
    """仅在显式写侧启动流程调用；预检和查询本身不建库。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS upload_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_path TEXT NOT NULL UNIQUE,
                stage TEXT NOT NULL DEFAULT 'staged',
                remote_filename TEXT,
                bvid TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL,
                last_error TEXT,
                title TEXT DEFAULT '',
                description TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS upload_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_path TEXT NOT NULL,
                stage TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        migrate_upload_columns(conn)
        conn.commit()
    finally:
        conn.close()


def migrate_upload_columns(conn: sqlite3.Connection) -> None:
    """Called only on explicit writes/startup; GET never repairs schema."""
    columns = {row[1] for row in conn.execute('PRAGMA table_info(upload_queue)')}
    for name, sql_type in {'tid': 'INTEGER', 'cover_path': 'TEXT', 'cover_url': 'TEXT', 'published_at': 'REAL',
                           'topic': 'TEXT', 'season': 'TEXT', 'aid': 'INTEGER',
                           'season_status': 'TEXT', 'season_error': 'TEXT',
                           'dynamic': "TEXT NOT NULL DEFAULT ''"}.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE upload_queue ADD COLUMN {name} {sql_type}')
