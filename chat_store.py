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
DEFAULT_RECENT_MESSAGES = 20
DEFAULT_SUMMARY_EVERY = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    scope   TEXT    NOT NULL,
    role    TEXT    NOT NULL,
    content TEXT    NOT NULL,
    ts      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(scope, id);
CREATE TABLE IF NOT EXISTS summaries (
    scope       TEXT PRIMARY KEY,
    summary     TEXT NOT NULL,
    covered_id  INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL
);
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


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        value = default
    return max(low, min(high, value))


class ChatStore:
    """按会话隔离的短期记忆。

    scope 约定：群聊 `group:<group_openid>`，单聊 `c2c:<user_openid>`。
    """

    def __init__(
        self,
        db_path: str = DB_PATH,
        max_messages: int | None = None,
        memory_minutes: float | None = None,
        recent_messages: int | None = None,
        summary_every: int | None = None,
    ):
        self.db_path = db_path
        self.max_messages = max(2, int(max_messages or _env_max_messages()))
        self.ttl_seconds = max(
            0.0, float(memory_minutes or _env_memory_minutes()) * 60.0
        )
        self.recent_messages = max(
            4,
            min(100, int(recent_messages or _env_int(
                "QQ_BOT_CHAT_RECENT", DEFAULT_RECENT_MESSAGES, 4, 100
            ))),
        )
        self.summary_every = max(
            10,
            min(500, int(summary_every or _env_int(
                "QQ_BOT_CHAT_SUMMARY_EVERY", DEFAULT_SUMMARY_EVERY, 10, 500
            ))),
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

    async def summary(self, scope: str) -> str:
        return await asyncio.to_thread(self._summary_sync, scope)

    async def summary_state(self, scope: str) -> tuple[str, int]:
        return await asyncio.to_thread(self._summary_state_sync, scope)

    async def should_summarize(self, scope: str) -> bool:
        return await asyncio.to_thread(self._should_summarize_sync, scope)

    async def summary_input(self, scope: str):
        """返回 (旧摘要, 已覆盖 id, 待摘要的早期消息)。最近 N 条永远不进来。"""
        return await asyncio.to_thread(self._summary_input_sync, scope)

    async def set_summary(self, scope: str, summary: str, covered_id: int) -> None:
        await asyncio.to_thread(
            self._set_summary_sync, scope, summary, covered_id
        )

    # ---------------- 同步实现 ----------------

    def _history_sync(self, scope: str) -> list[dict]:
        cutoff = time.time() - self.ttl_seconds
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE scope = ? AND ts >= ? ORDER BY id DESC LIMIT ?",
                (scope, cutoff, self.recent_messages),
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

    def _summary_sync(self, scope: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary FROM summaries WHERE scope = ?", (scope,)
            ).fetchone()
        return (row["summary"] or "") if row else ""

    def _summary_state_sync(self, scope: str) -> tuple[str, int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary, covered_id FROM summaries WHERE scope = ?",
                (scope,),
            ).fetchone()
        if not row:
            return "", 0
        return (row["summary"] or ""), int(row["covered_id"] or 0)

    def _should_summarize_sync(self, scope: str) -> bool:
        cutoff = time.time() - self.ttl_seconds
        summary, covered = self._summary_state_sync(scope)
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE scope = ? AND ts >= ?",
                (scope, cutoff),
            ).fetchone()
            early_count = int(total["n"] if total else 0) - self.recent_messages
            if early_count <= 0:
                return False
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM ("
                "  SELECT id FROM messages WHERE scope = ? AND ts >= ? "
                "  ORDER BY id ASC LIMIT ?"
                ") WHERE id > ?",
                (scope, cutoff, early_count, covered),
            ).fetchone()
        return bool(row and int(row["n"]) >= self.summary_every)

    def _summary_input_sync(self, scope: str):
        cutoff = time.time() - self.ttl_seconds
        summary, covered = self._summary_state_sync(scope)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content FROM messages "
                "WHERE scope = ? AND ts >= ? ORDER BY id ASC",
                (scope, cutoff),
            ).fetchall()
        early = rows[: -self.recent_messages] if len(rows) > self.recent_messages else []
        early = [r for r in early if int(r["id"]) > covered]
        if not early:
            return summary, covered, []
        early = early[:120]  # 控制单次 prompt，不要一次塞几百条
        covered_id = int(early[-1]["id"])
        messages = [
            {"role": r["role"], "content": r["content"]} for r in early
        ]
        return summary, covered_id, messages

    def _set_summary_sync(self, scope: str, summary: str, covered_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO summaries(scope, summary, covered_id, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(scope) DO UPDATE SET "
                "summary = excluded.summary, covered_id = excluded.covered_id, "
                "updated_at = excluded.updated_at",
                (scope, (summary or "").strip(), int(covered_id), time.time()),
            )

    def _clear_sync(self, scope: str) -> int:
        with self._connect() as conn:
            conn.execute("DELETE FROM summaries WHERE scope = ?", (scope,))
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
