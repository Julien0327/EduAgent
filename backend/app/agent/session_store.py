"""会话存储（Phase 2 / S5，§7）：sessions/messages 表读写（V1 已建表）。

多轮会话机制（§7.1）：POST /api/chat 与 /api/tasks 带 session_id（缺省新建）；
消息原文全量落库，入上下文时每轮截断 2000 字符（§7.2）。

追问通道（F1）：assistant 追问消息以「【追问】」前缀落库——会话内追问次数
由消息计数得出，≤2 轮追问、超限降级默认值执行（planner/chat 协同）。
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from backend.app.storage import db

CONTEXT_CHAR_LIMIT = 2000  # §7.2：每轮截断入上下文（原文全量落库）
MAX_CLARIFY_ROUNDS = 2     # F1：至多 2 轮追问，超限降级默认值
CLARIFY_PREFIX = "【追问】"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    """统一连接入口：先幂等应用迁移（测试/CLI 环境不经 lifespan 时保证表存在）。"""
    path = db.get_db_path()
    db.apply_migrations(path)
    return db.connect(path, check_same_thread=False)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_session(title: str | None = None, conn: sqlite3.Connection | None = None) -> str:
    """新建会话，返回 session_id（标题缺省取首条消息前 20 字，落消息时补）。"""
    session_id = uuid.uuid4().hex[:12]
    c = conn or _connect()
    try:
        with _lock:
            c.execute(
                "INSERT INTO sessions (session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, title, _now(), _now()),
            )
            c.commit()
    finally:
        if conn is None:
            c.close()
    return session_id


def add_message(session_id: str, role: str, content: str,
                task_id: str | None = None) -> None:
    """追加一条消息（原文全量落库）并更新会话时间戳。"""
    c = _connect()
    try:
        with _lock:
            c.execute(
                "INSERT INTO messages (session_id, role, content, task_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, task_id, _now()),
            )
            c.execute("UPDATE sessions SET updated_at=? WHERE session_id=?",
                      (_now(), session_id))
            # 标题缺省：首条用户消息前 20 字
            row = c.execute(
                "SELECT title FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if row and not row["title"] and role == "user":
                c.execute("UPDATE sessions SET title=? WHERE session_id=?",
                          (content[:20], session_id))
            c.commit()
    finally:
        c.close()


def list_sessions(limit: int = 50) -> list[dict]:
    """会话列表（§10.2：id/标题/时间 + 消息数）。"""
    c = _connect()
    try:
        rows = c.execute(
            """SELECT s.session_id, s.title, s.created_at, s.updated_at,
                      (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.session_id) AS msg_count
               FROM sessions s ORDER BY s.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def get_session(session_id: str) -> dict | None:
    """会话详情：消息回放 + 关联任务卡（§7.2：前端会话切换时回放）。"""
    c = _connect()
    try:
        s = c.execute(
            "SELECT session_id, title, created_at, updated_at FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if s is None:
            return None
        messages = [
            dict(r) for r in c.execute(
                "SELECT msg_id, role, content, task_id, created_at FROM messages "
                "WHERE session_id=? ORDER BY msg_id", (session_id,))
        ]
        tasks = [
            dict(r) for r in c.execute(
                "SELECT task_id, prompt, status, workflow, error FROM tasks "
                "WHERE session_id=? ORDER BY created_at", (session_id,))
        ]
        return {**dict(s), "messages": messages, "tasks": tasks}
    finally:
        c.close()


def context_window(session_id: str, max_turns: int = 6) -> list[dict]:
    """最近 N 轮消息入上下文（每轮截断 2000 字符，§7.2；原文不受影响）。"""
    c = _connect()
    try:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY msg_id DESC LIMIT ?",
            (session_id, max_turns * 2),
        ).fetchall()
    finally:
        c.close()
    return [
        {"role": r["role"],
         "content": r["content"][:CONTEXT_CHAR_LIMIT]}
        for r in reversed(rows)
    ]


def clarify_count(session_id: str) -> int:
    """会话内已发生的追问轮数（F1：≤2 轮，超限降级默认值执行）。"""
    c = _connect()
    try:
        row = c.execute(
            "SELECT COUNT(*) n FROM messages WHERE session_id=? AND role='assistant' "
            "AND content LIKE ?",
            (session_id, CLARIFY_PREFIX + "%"),
        ).fetchone()
        return int(row["n"])
    finally:
        c.close()
