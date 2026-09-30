"""
人格聊天的对话记忆（按会话隔离）。

职责：
  - 每个会话（群 / 单聊）各自保存最近 N 条消息，**互不串台**
  - 超出上限时裁掉最早的记录

目录结构：
  data/chat.db    SQLite

为什么单独一个库：`images.db` 装的是图库这种「资产」，对话上下文只是可丢弃的
临时记忆。混在一起会让备份、清理、迁移都变复杂，也没必要跟着图片一起上云备份。

配置：
  QQ_BOT_CHAT_MEMORY  每个会话保留的消息条数上限（默认 100）
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time

log = logging.getLogger("qqbot.chat")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "chat.db")

MAX_MESSAGES = int(os.getenv("QQ_BOT_CHAT_MEMORY", "100") or 100)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    scope   TEXT    NOT NULL,
    role    TEXT    NOT NULL,
    content TEXT    NOT NULL,
    ts      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(scope, id);
"""


class ChatStore:
    """按会话隔离的短期记忆。

    scope 约定：群聊 `group:<group_openid>`，单聊 `c2c:<user_openid>`。
    """

    def __init__(self, db_path: str = DB_PATH, max_messages: int = MAX_MESSAGES):
        self.db_path = db_path
        self.max_messages = max(2, int(max_messages))
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------- 对外（异步，沿用 image_store 的 to_thread 风格） ----------------

    async def history(self, scope: str) -> list[dict]:
        """取该会话的上下文，按时间正序，形如 `[{"role": "user", "content": "…"}]`。"""
        return await asyncio.to_thread(self._history_sync, scope)

    async def append(self, scope: str, user_text: str, reply_text: str | None) -> None:
        """追加一轮对话。`reply_text` 为空表示这一轮机器人没回复，只记用户那句。"""
        await asyncio.to_thread(self._append_sync, scope, user_text, reply_text)

    async def clear(self, scope: str) -> int:
        return await asyncio.to_thread(self._clear_sync, scope)

    async def count(self, scope: str) -> int:
        return await asyncio.to_thread(self._count_sync, scope)

    # ---------------- 同步实现 ----------------

    def _history_sync(self, scope: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE scope = ? "
                "ORDER BY id DESC LIMIT ?",
                (scope, self.max_messages),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def _append_sync(self, scope: str, user_text: str, reply_text: str | None) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(scope, role, content, ts) VALUES (?, ?, ?, ?)",
                (scope, "user", user_text, now),
            )
            if reply_text:
                conn.execute(
                    "INSERT INTO messages(scope, role, content, ts) "
                    "VALUES (?, ?, ?, ?)",
                    (scope, "assistant", reply_text, now),
                )
            # 裁剪：只留最近 max_messages 条，避免库无限增长
            conn.execute(
                "DELETE FROM messages WHERE scope = ? AND id NOT IN ("
                "    SELECT id FROM messages WHERE scope = ? ORDER BY id DESC LIMIT ?"
                ")",
                (scope, scope, self.max_messages),
            )

    def _clear_sync(self, scope: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM messages WHERE scope = ?", (scope,))
        return cur.rowcount or 0

    def _count_sync(self, scope: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE scope = ?", (scope,)
            ).fetchone()
        return int(row["n"]) if row else 0
