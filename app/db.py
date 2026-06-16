"""SQLite 数据访问层。

设计原则：
- 每次操作 connect → execute → close，不长持连接（兼容多进程共享）。
- 启用 WAL 提升并发读写。
- event_id / qq_msg_id 重复时由 UNIQUE 约束兜底，不抛未处理异常。
- 时间统一使用 UTC ISO 字符串。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """返回 UTC ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent_dir(path: str) -> None:
    """确保 SQLite 文件所在的目录存在。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """获取 SQLite 连接（短连接），并自动 commit/close。"""
    settings = get_settings()
    _ensure_parent_dir(settings.db_path)
    conn = sqlite3.connect(settings.db_path, timeout=10.0)
    try:
        conn.row_factory = sqlite3.Row
        # WAL 模式提升多进程并发读写体验
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """启动时建表（幂等）。"""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                user_openid TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notify_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                project TEXT NOT NULL,
                agent TEXT NOT NULL,
                status TEXT NOT NULL,
                exit_code INTEGER,
                duration_sec INTEGER,
                cwd TEXT,
                host TEXT,
                branch TEXT,
                commit_id TEXT,
                summary TEXT,
                log_tail TEXT,
                target_openid TEXT,
                qq_message_id TEXT,
                send_status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inbound_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_msg_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                user_openid TEXT,
                content TEXT,
                raw_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_notify_events_created
                ON notify_events(created_at DESC);
            """
        )
    logger.info("database initialized at %s", get_settings().db_path)


# ---------------------------------------------------------------------------
# notify_events
# ---------------------------------------------------------------------------


def event_exists(event_id: str) -> bool:
    """检查事件是否已存在。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM notify_events WHERE event_id = ? LIMIT 1",
            (event_id,),
        ).fetchone()
    return row is not None


def insert_notify_event(
    *,
    event_id: str,
    project: str,
    agent: str,
    status: str,
    exit_code: Optional[int],
    duration_sec: Optional[int],
    cwd: Optional[str],
    host: Optional[str],
    branch: Optional[str],
    commit: Optional[str],
    summary: Optional[str],
    log_tail: Optional[str],
    target_openid: Optional[str],
    qq_message_id: Optional[str],
    send_status: str,
    error: Optional[str] = None,
) -> bool:
    """写入通知事件。

    Returns:
        True 成功写入，False 表示 event_id 重复。
    """
    now = _utc_now_iso()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO notify_events (
                    event_id, project, agent, status, exit_code, duration_sec,
                    cwd, host, branch, commit_id, summary, log_tail,
                    target_openid, qq_message_id, send_status, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    project,
                    agent,
                    status,
                    exit_code,
                    duration_sec,
                    cwd,
                    host,
                    branch,
                    commit,
                    summary,
                    log_tail,
                    target_openid,
                    qq_message_id,
                    send_status,
                    error,
                    now,
                ),
            )
        return True
    except sqlite3.IntegrityError:
        # event_id 唯一约束冲突，吞掉异常
        logger.warning("notify_event already exists: %s", event_id)
        return False


def list_events(limit: int = 20) -> list[dict[str, Any]]:
    """按 created_at 倒序列出最近事件。"""
    limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, event_id, project, agent, status, exit_code, duration_sec,
                   cwd, host, branch, commit_id, summary, target_openid,
                   qq_message_id, send_status, error, created_at
            FROM notify_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------


def upsert_target(name: str, user_openid: str) -> None:
    """upsert 目标。"""
    now = _utc_now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO targets (name, user_openid, enabled, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                user_openid = excluded.user_openid,
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (name, user_openid, now, now),
        )


def get_target(name: str = "me") -> Optional[dict[str, Any]]:
    """读取指定 name 的启用目标。"""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, name, user_openid, enabled, created_at, updated_at
            FROM targets
            WHERE name = ? AND enabled = 1
            LIMIT 1
            """,
            (name,),
        ).fetchone()
    return dict(row) if row else None


def list_targets() -> list[dict[str, Any]]:
    """列出全部目标。"""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, user_openid, enabled, created_at, updated_at
            FROM targets
            ORDER BY id ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# inbound_messages
# ---------------------------------------------------------------------------


def insert_inbound_message(
    *,
    qq_msg_id: str,
    event_type: str,
    user_openid: Optional[str],
    content: Optional[str],
    raw_json: str,
) -> bool:
    """写入入站消息。重复 qq_msg_id 静默忽略。

    Returns:
        True 表示写入，False 表示重复。
    """
    now = _utc_now_iso()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO inbound_messages (
                    qq_msg_id, event_type, user_openid, content, raw_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (qq_msg_id, event_type, user_openid, content, raw_json, now),
            )
        return True
    except sqlite3.IntegrityError:
        logger.debug("inbound message already exists: %s", qq_msg_id)
        return False
