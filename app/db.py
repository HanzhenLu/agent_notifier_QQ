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
                name TEXT NOT NULL,
                user_openid TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                agent_token_hash TEXT,
                token_prefix TEXT,
                last_used_at TEXT
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

        # ------------------------------------------------------------------
        # 软迁移：为老库补齐 per-user agent_token 相关字段
        # ------------------------------------------------------------------
        for stmt in (
            "ALTER TABLE targets ADD COLUMN agent_token_hash TEXT",
            "ALTER TABLE targets ADD COLUMN token_prefix TEXT",
            "ALTER TABLE targets ADD COLUMN last_used_at TEXT",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                # duplicate column 表示已经迁移过
                pass

        # 老库 targets.name 原本带 UNIQUE 约束（旧设计 name='me'），
        # 新设计里 name 是用户别名，不应唯一。检查并重建表。
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='targets'"
        ).fetchone()
        if sql_row and "UNIQUE" in (sql_row["sql"] or "").upper():
            logger.info("migrating targets table: dropping UNIQUE on name")
            conn.executescript(
                """
                CREATE TABLE targets__new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    user_openid TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    agent_token_hash TEXT,
                    token_prefix TEXT,
                    last_used_at TEXT
                );
                INSERT INTO targets__new (
                    id, name, user_openid, enabled, created_at, updated_at,
                    agent_token_hash, token_prefix, last_used_at
                )
                SELECT
                    id, name, user_openid, enabled, created_at, updated_at,
                    agent_token_hash, token_prefix, last_used_at
                FROM targets;
                DROP TABLE targets;
                ALTER TABLE targets__new RENAME TO targets;
                """
            )

        # 每个 user_openid 至多一行 target
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_targets_user_openid "
            "ON targets(user_openid)"
        )
        # token_hash 唯一（允许 NULL，老行迁移期可能没 token）
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_targets_token_hash "
            "ON targets(agent_token_hash) WHERE agent_token_hash IS NOT NULL"
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


def _target_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "user_openid": row["user_openid"],
        "enabled": row["enabled"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "token_prefix": row["token_prefix"],
        "last_used_at": row["last_used_at"],
    }


def upsert_target_with_token(
    *,
    user_openid: str,
    name: str,
    agent_token_hash: str,
    token_prefix: str,
) -> dict[str, Any]:
    """根据 ``user_openid`` upsert target，并写入新生成的 token hash。

    若该 ``user_openid`` 已存在：更新 token、name、updated_at、enable=1。
    若不存在：新建一行。

    Returns:
        新写入/更新后的 target（dict 形式，含 token_prefix 等元信息，但不含 hash）。
    """
    now = _utc_now_iso()
    with _connect() as conn:
        # 通过 user_openid 唯一索引完成 upsert
        conn.execute(
            """
            INSERT INTO targets (
                name, user_openid, enabled, created_at, updated_at,
                agent_token_hash, token_prefix, last_used_at
            )
            VALUES (?, ?, 1, ?, ?, ?, ?, NULL)
            ON CONFLICT(user_openid) DO UPDATE SET
                name = excluded.name,
                enabled = 1,
                updated_at = excluded.updated_at,
                agent_token_hash = excluded.agent_token_hash,
                token_prefix = excluded.token_prefix
            """,
            (name, user_openid, now, now, agent_token_hash, token_prefix),
        )
        row = conn.execute(
            """
            SELECT id, name, user_openid, enabled, created_at, updated_at,
                   token_prefix, last_used_at
            FROM targets WHERE user_openid = ? LIMIT 1
            """,
            (user_openid,),
        ).fetchone()
    return _target_row_to_dict(row) if row else {}


def get_target_by_token_hash(token_hash: str) -> Optional[dict[str, Any]]:
    """根据 token sha256 查询启用的 target。"""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, name, user_openid, enabled, created_at, updated_at,
                   token_prefix, last_used_at
            FROM targets
            WHERE agent_token_hash = ? AND enabled = 1
            LIMIT 1
            """,
            (token_hash,),
        ).fetchone()
    return _target_row_to_dict(row) if row else None


def get_target_by_openid(user_openid: str) -> Optional[dict[str, Any]]:
    """根据 user_openid 查询 target（含已禁用的）。"""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, name, user_openid, enabled, created_at, updated_at,
                   token_prefix, last_used_at
            FROM targets
            WHERE user_openid = ?
            LIMIT 1
            """,
            (user_openid,),
        ).fetchone()
    return _target_row_to_dict(row) if row else None


def disable_target_by_openid(user_openid: str) -> bool:
    """按 user_openid 禁用 target，并清空 token。

    Returns:
        是否真的影响到一行（False 表示该用户从未绑定过）。
    """
    now = _utc_now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE targets
            SET enabled = 0,
                agent_token_hash = NULL,
                updated_at = ?
            WHERE user_openid = ? AND enabled = 1
            """,
            (now, user_openid),
        )
        return cur.rowcount > 0


def touch_target_last_used(target_id: int) -> None:
    """更新 target 的 last_used_at。失败不抛。"""
    now = _utc_now_iso()
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE targets SET last_used_at = ? WHERE id = ?",
                (now, target_id),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("touch_target_last_used failed: %s", e)


def list_targets() -> list[dict[str, Any]]:
    """列出全部目标（不返回 token hash）。"""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, user_openid, enabled, created_at, updated_at,
                   token_prefix, last_used_at
            FROM targets
            ORDER BY id ASC
            """
        ).fetchall()
    return [_target_row_to_dict(r) for r in rows]


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
