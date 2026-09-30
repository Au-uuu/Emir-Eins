"""
人格聊天的对话记忆（按会话隔离）。

职责：
  - 每个会话（群 / 单聊）各自保存最近的消息，**互不串台**
  - 双重过期：条数上限（默认 100 条）+ 存活时间上限（默认 20 分钟）
  - 只发了图片这类「无正文」的消息也会记一笔，让紧接着的下一句能接得上

目录结构：
  data/chat.db    SQLite

为什么单独一个库：`images.db` 装的是图库这种「资产」，对话上下文只是可丢弃的
临时记忆。混在一起会让备份、清理、迁移都变复杂，也没必要跟着图片一起上云备份。

配置：
  QQ_BOT_CHAT_MEMORY          每个会话保留的消息条数上限（默认 100）
  QQ_BOT_CHAT_MEMORY_MINUTES  上下文的存活时间上限（分钟，默认 20）
                              —— 超过这个时间的旧消息算过期，不再喂给模型

⚠️ 配置一律**惰性读取**（在 __init__ 里读，不在 import 期读）：
   `bot.py` 的 `load_dotenv()` 在 import 语句之后才执行，import 期间读环境变量
   只会读到空值。
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

DEFAULT_MAX_MESSAGES = 100
DEFAULT_MEMORY_MINUTES = 20.0

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


def _env_max_messages() -> int:
    try:
        return int(os.getenv("QQ_BOT_CHAT_MEMORY", "") or DEFAULT_MAX_MESSAGES)
    except ValueError:
        return DEFAULT_MAX_MESSAGES


def _env_memory_minutes() -> float:
    try:
        return float(
            os.getenv("QQ_BOT_CHAT_MEMORY_MINUTES", "") or DEFAULT_MEMORY_MINUTES
        )
    except ValueError:
        return DEFAULT_MEMORY_MINUTES


class ChatStore:
    """按会话隔离的短期记忆。

    scope 约定：群聊 `group:<group_openid>`，单聊 `c2c:<user_openid>`。
    """

    def __init__(
        self,
        db_path: str = DB_PATH,
        max_messages: int | None = None,
        memory_minutes: float | None = None,
    ):
        self.db_path = db_path
        self.max_messages = max(2, int(max_messages or _env_max_messages()))
        self.ttl_seconds = max(
            0.0, float(memory_minutes or _env_memory_minutes()) * 60.0
        )
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------- 对外（异步，沿用 image_store 的 to_thread 风格） ----------------

    async def history(self, scope: str) -> list[dict]:
        """取该会话**尚未过期**的上下文，按时间正序，形如 `[{"role","content"}]`。"""
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
        cutoff = time.time() - self.ttl_seconds
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE scope = ? AND ts >= ? ORDER BY id DESC LIMIT ?",
                (scope, cutoff, self.max_messages),
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
            # 过期清理一：活得太久
            conn.execute(
                "DELETE FROM messages WHERE scope = ? AND ts < ?",
                (scope, now - self.ttl_seconds),
            )
            # 过期清理二：只留最近 max_messages 条，避免库无限增长
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
        cutoff = time.time() - self.ttl_seconds
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE scope = ? AND ts >= ?",
                (scope, cutoff),
            ).fetchone()
        return int(row["n"]) if row else 0
