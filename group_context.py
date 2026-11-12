"""
群聊上下文缓冲：把群里**没 @ 机器人**的消息也记下来，等被 @ 时作为背景。

为什么需要
----------
`chat_store` 只记「用户 ↔ 机器人」的一问一答，群里其他人聊了什么、刚才在接什么梗，
模型完全不知道。群主开了「接收所有消息」后，机器人其实能看到全群消息——这些消息
不该拿来主动回复，但可以作为**背景**注入下一次被 @ 时的提示词。

边界
----
- 只存**文字**（图片/视频只存 `[图片]` / `[视频]` 占位，不下载、不占盘）；
- 只存**群聊**，单聊不进来；
- 默认保留最近 30 条 / 30 分钟，超长截断；不写进 `chat.db`，不参与人格记忆；
- 命令（`/图库`、`/来只` 等）不入缓冲，避免把指令当聊天内容；
- 默认开启，`QQ_BOT_GROUP_CONTEXT=0` 可整体关闭。

目录结构
--------
`data/group_context.db`（SQLite；`data/` 已被 .gitignore 忽略）

配置（惰性读取）
----------------
| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_BOT_GROUP_CONTEXT` | `1` | 总开关 |
| `QQ_BOT_GROUP_CONTEXT_MAX` | `30` | 最多取多少条 |
| `QQ_BOT_GROUP_CONTEXT_MINUTES` | `30` | 只取最近多少分钟 |
| `QQ_BOT_GROUP_CONTEXT_CHARS` | `2000` | 拼进提示词的总字数上限 |
| `QQ_BOT_GROUP_CONTEXT_DB` | `data/group_context.db` | 数据库路径（测试用临时路径） |
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time

log = logging.getLogger("qqbot.group_context")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "group_context.db")

DEFAULT_MAX_MESSAGES = 30
DEFAULT_MINUTES = 30.0
DEFAULT_MAX_CHARS = 2000
MAX_LINE_CHARS = 200

_HEADER = "（群聊最近消息，按时间正序，仅供参考；不要逐条复述，也不要把背景当成在叫你：）"
_FOOTER = "（以上是群聊背景；下面是需要你回应的消息）"

_TOPIC_PROMPT = (
    "你是群聊话题整理助手。根据最近 {minutes} 分钟的群聊记录，提取当前仍在聊的 1~3 个话题。\n"
    "要求：\n"
    "1. 只列当前话题，已经结束的旧话题不要列；超过 {minutes} 分钟的内容不要当当前话题；\n"
    "2. 按最近消息的优先级排序，每条不超过 20 字；\n"
    "3. 忽略广告、卡片、链接、命令、图片/视频占位；\n"
    "4. 没有明确话题就输出空数组。\n"
    '只输出严格 JSON，例如 {"topics":["话题一","话题二"]}，不要输出其他字。'
)

_topics_cache: dict[str, tuple[float, str]] = {}
_TOPICS_TTL = 60.0


def _parse_topics(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    data = None
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except ValueError:
                data = None
    items = []
    if isinstance(data, dict):
        items = data.get("topics") or []
    elif isinstance(data, list):
        items = data
    if not items:
        # 模型偶尔不按 JSON 输出，按行/顿号兜底
        items = re.split(r"[\n、,，]+", text)
    out = []
    for item in items:
        item = re.sub(r"^[\s\-*·\d.、]+", "", str(item)).strip().strip('"“”')
        if not item or item.startswith("{"):
            continue
        if item in ("无", "没有", "暂无话题", "无明确话题"):
            continue
        if len(item) > 24:
            item = item[:24].rstrip()
        if item not in out:
            out.append(item)
    return out[:3]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS group_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid  TEXT    NOT NULL,
    msg_id        TEXT    NOT NULL,
    openid        TEXT    NOT NULL DEFAULT '',
    display_name  TEXT    NOT NULL DEFAULT '',
    content       TEXT    NOT NULL,
    ts            REAL    NOT NULL,
    UNIQUE(group_openid, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_group_ctx
    ON group_messages(group_openid, id);
"""


def enabled() -> bool:
    return (os.getenv("QQ_BOT_GROUP_CONTEXT", "1") or "1").strip().lower() not in (
        "0", "false", "off", "no", ""
    )


def proactive_window_minutes() -> float:
    """随机接话只看最近多少分钟的群聊；默认 10 分钟，避免接旧话题。"""
    try:
        value = float(os.getenv("QQ_BOT_PROACTIVE_WINDOW_MINUTES", "10") or 10)
    except ValueError:
        value = 10.0
    return max(1.0, min(30.0, value))


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        value = default
    return max(low, min(high, value))


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        value = default
    return max(low, min(high, value))


class GroupContextStore:
    """最近群消息的环形缓冲（SQLite，按群隔离）。"""

    def __init__(
        self,
        db_path: str | None = None,
        max_messages: int | None = None,
        minutes: float | None = None,
        max_chars: int | None = None,
    ):
        self.db_path = (
            db_path
            or (os.getenv("QQ_BOT_GROUP_CONTEXT_DB", "") or "").strip()
            or DB_PATH
        )
        self.max_messages = max_messages or _env_int(
            "QQ_BOT_GROUP_CONTEXT_MAX", DEFAULT_MAX_MESSAGES, 3, 100
        )
        self.ttl_seconds = (
            minutes
            if minutes is not None
            else _env_float("QQ_BOT_GROUP_CONTEXT_MINUTES", DEFAULT_MINUTES, 1, 1440)
        ) * 60.0
        self.max_chars = max_chars or _env_int(
            "QQ_BOT_GROUP_CONTEXT_CHARS", DEFAULT_MAX_CHARS, 200, 8000
        )
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        # 惰性建库：测试/关闭群聊上下文时不碰 data/ 里的真实库
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        if not self._ready:
            conn.executescript(_SCHEMA)
            # 旧库补列（群昵称/QQ 昵称）；新库建表时已带
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(group_messages)")}
            if "display_name" not in cols:
                conn.execute(
                    "ALTER TABLE group_messages "
                    "ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
                )
            self._ready = True
        return conn

    # ---------------- 对外 ----------------

    async def remember(
        self,
        group_openid: str,
        msg_id: str,
        openid: str,
        content: str,
        display_name: str = "",
    ) -> None:
        """记一条群消息。空内容/命令由调用方过滤，这里只做兜底。"""
        if not enabled() or not group_openid:
            return
        text = (content or "").strip()
        if not text:
            return
        await asyncio.to_thread(
            self._remember_sync,
            group_openid,
            msg_id,
            openid,
            display_name,
            text[:500],
        )

    async def render(
        self,
        group_openid: str,
        exclude_id: str = "",
        limit: int | None = None,
        since_ts: float = 0.0,
    ) -> str:
        """取最近群消息拼成提示词块；没有可用的返回空串。"""
        if not enabled() or not group_openid:
            return ""
        return await asyncio.to_thread(
            self._render_sync,
            group_openid,
            exclude_id or "",
            limit,
            since_ts,
        )

    async def clear(self, group_openid: str) -> int:
        return await asyncio.to_thread(self._clear_sync, group_openid)

    async def count(self, group_openid: str) -> int:
        return await asyncio.to_thread(self._count_sync, group_openid)

    async def recent(
        self, group_openid: str, limit: int = 60, since_ts: float = 0.0
    ) -> list[dict]:
        """取最近消息（旧→新），供自动学习/调试；不排除任何 msg_id。"""
        if not group_openid:
            return []
        return await asyncio.to_thread(
            self._recent_sync, group_openid, limit, since_ts
        )

    async def recent_members(
        self, group_openid: str, limit: int = 80
    ) -> list[dict]:
        """
        最近活跃成员（按发言条数降序），给「群友画像」注入用。

        返回 [{"openid","name","count"}]；昵称取该成员最近一条的 display_name。
        只读群聊缓冲，不额外建表。
        """
        rows = await self.recent(group_openid, limit=limit)
        stats: dict[str, dict] = {}
        for r in rows:
            oid = (r.get("openid") or "").strip()
            if not oid:
                continue
            item = stats.setdefault(oid, {"openid": oid, "name": "", "count": 0})
            name = (r.get("display_name") or "").strip()
            if name:
                item["name"] = name[:24]
            item["count"] += 1
        return sorted(stats.values(), key=lambda x: x["count"], reverse=True)

    async def active_groups(self, minutes: float = 180.0) -> list[str]:
        """最近 N 分钟有消息的群（自动学习用）。"""
        return await asyncio.to_thread(self._active_groups_sync, minutes)

    async def topics(
        self, group_openid: str, limit: int = 20, minutes: float | None = None
    ) -> str:
        """用主模型把最近群聊压成 1~3 个当前话题，缓存 60 秒。

        只给随机接话用：让模型知道“现在在聊什么”，避免接旧话题。
        minutes 限定只看最近多少分钟；失败返回空串。
        """
        if not enabled() or not group_openid:
            return ""
        if (os.getenv("QQ_BOT_GROUP_TOPICS", "1") or "1").strip().lower() in (
            "0", "false", "off", "no", "",
        ):
            return ""
        window = float(minutes) if minutes is not None else proactive_window_minutes()
        window = max(1.0, min(30.0, window))
        cache_key = f"{group_openid}:{int(window)}"
        now = time.monotonic()
        hit = _topics_cache.get(cache_key)
        if hit and now - hit[0] < _TOPICS_TTL:
            return hit[1]

        since_ts = time.time() - window * 60.0
        rows = await self.recent(group_openid, limit=limit, since_ts=since_ts)
        lines: list[str] = []
        for row in rows:
            text = " ".join((row.get("content") or "").split())
            if not text or text.startswith("/"):
                continue
            if text.startswith("[") and text.endswith("]"):
                continue
            who = (row.get("display_name") or "").strip() or "群友"
            clock = time.strftime("%H:%M", time.localtime(row.get("ts") or 0))
            lines.append(f"[{clock}] {who}: {text[:160]}")
        transcript = "\n".join(lines[-limit:])[-3000:]
        if not transcript:
            _topics_cache[cache_key] = (now, "")
            return ""

        raw = ""
        try:
            # 惰性导入，避免 group_context <-> persona 循环依赖
            import persona

            raw = await persona.raw_complete(
                _TOPIC_PROMPT.replace("{minutes}", str(int(window))),
                transcript,
                session=f"topics:{group_openid}",
                kind="summary",
            ) or ""
        except Exception as exc:  # noqa: BLE001
            log.warning("[群话题] 总结失败 %s：%s", group_openid[:8], exc)
        topics = _parse_topics(raw)
        text = " / ".join(topics)
        _topics_cache[cache_key] = (now, text)
        return text

    # ---------------- 同步实现 ----------------

    def _remember_sync(
        self,
        group_openid: str,
        msg_id: str,
        openid: str,
        display_name: str,
        content: str,
    ) -> None:
        now = time.time()
        mid = (msg_id or "").strip() or f"auto:{time.time_ns()}"
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO group_messages"
                "(group_openid, msg_id, openid, display_name, content, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    group_openid,
                    mid,
                    (openid or "").strip(),
                    (display_name or "").strip()[:24],
                    content,
                    now,
                ),
            )
            conn.execute(
                "DELETE FROM group_messages WHERE group_openid = ? AND ts < ?",
                (group_openid, now - self.ttl_seconds),
            )
            conn.execute(
                "DELETE FROM group_messages WHERE group_openid = ? AND id NOT IN ("
                "    SELECT id FROM group_messages WHERE group_openid = ? "
                "    ORDER BY id DESC LIMIT ?"
                ")",
                (group_openid, group_openid, self.max_messages),
            )

    def _render_sync(
        self,
        group_openid: str,
        exclude_id: str,
        limit: int | None,
        since_ts: float = 0.0,
    ) -> str:
        take = max(1, min(int(limit or self.max_messages), self.max_messages))
        cutoff = time.time() - self.ttl_seconds
        if since_ts:
            cutoff = max(cutoff, float(since_ts))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT msg_id, openid, display_name, content, ts FROM group_messages "
                "WHERE group_openid = ? AND ts >= ? AND msg_id <> ? "
                "ORDER BY id DESC LIMIT ?",
                (group_openid, cutoff, exclude_id, take),
            ).fetchall()
        if not rows:
            return ""

        lines: list[str] = []
        budget = max(200, self.max_chars - len(_HEADER) - len(_FOOTER) - 4)
        for row in rows:  # 新 → 旧，装到预算为止
            text = " ".join((row["content"] or "").split())
            if not text or text.startswith("/"):
                continue
            if len(text) > MAX_LINE_CHARS:
                text = text[:MAX_LINE_CHARS].rstrip() + "…"
            openid = row["openid"] or ""
            display_name = (row["display_name"] or "").strip()
            who = display_name or (f"用户{openid[-6:]}" if openid else "某群友")
            clock = time.strftime("%H:%M", time.localtime(row["ts"]))
            line = f"[{clock}] {who}: {text}"
            if lines and len(line) + 1 > budget:
                break
            lines.append(line)
            budget -= len(line) + 1
        if not lines:
            return ""
        lines.reverse()  # 旧 → 新
        return _HEADER + "\n" + "\n".join(lines) + "\n" + _FOOTER + "\n"

    def _recent_sync(
        self, group_openid: str, limit: int, since_ts: float
    ) -> list[dict]:
        cutoff = time.time() - self.ttl_seconds
        since = max(cutoff, float(since_ts or 0.0))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT openid, display_name, content, ts FROM group_messages "
                "WHERE group_openid = ? AND ts >= ? "
                "ORDER BY id DESC LIMIT ?",
                (group_openid, since, max(1, min(200, int(limit)))),
            ).fetchall()
        return [
            {
                "openid": r["openid"] or "",
                "display_name": r["display_name"] or "",
                "content": r["content"] or "",
                "ts": float(r["ts"]),
            }
            for r in reversed(rows)
        ]

    def _active_groups_sync(self, minutes: float) -> list[str]:
        cutoff = time.time() - max(1.0, float(minutes)) * 60.0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT group_openid, COUNT(*) AS n FROM group_messages "
                "WHERE ts >= ? GROUP BY group_openid HAVING n >= 3 "
                "ORDER BY n DESC",
                (cutoff,),
            ).fetchall()
        return [r["group_openid"] for r in rows]

    def _clear_sync(self, group_openid: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM group_messages WHERE group_openid = ?", (group_openid,)
            )
        return cur.rowcount or 0

    def _count_sync(self, group_openid: str) -> int:
        cutoff = time.time() - self.ttl_seconds
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM group_messages "
                "WHERE group_openid = ? AND ts >= ?",
                (group_openid, cutoff),
            ).fetchone()
        return int(row["n"]) if row else 0
