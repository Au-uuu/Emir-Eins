"""
本地记忆：群黑话、人物印象、群内称呼（按群隔离）。

设计
----
- `aliases`：用户自己登记的称呼（`/叫我 小明`），比 openid 尾号好用；
- `impressions`：bot 对这个人的印象（自动学习或管理员手写），只注入当前发言人；
- `slang`：群黑话/梗/缩写，按群隔离；手动条目优先于自动条目；
- `learn_state`：每个群上次自动学习的时间，避免重复喂同一批消息。

边界
----
- 只存**本群**内容，不跨群共享；删 `data/memory.db` 只丢记忆；
- 自动条目带 `source='auto'`，管理员可删；手动条目 `source='manual'`；
- 注入时只取当前发言人印象 + 当前消息/群背景里出现的黑话，不整本塞进去；
- 默认开启，`QQ_BOT_MEMORY=0` 可整体关闭；自动学习频率见
  `QQ_BOT_MEMORY_LEARN_MINUTES`。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time

log = logging.getLogger("qqbot.memory")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "memory.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS aliases (
    scope      TEXT NOT NULL,
    openid     TEXT NOT NULL,
    alias      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope, openid)
);
CREATE TABLE IF NOT EXISTS impressions (
    scope      TEXT NOT NULL,
    openid     TEXT NOT NULL,
    note       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'auto',
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope, openid)
);
CREATE TABLE IF NOT EXISTS slang (
    scope      TEXT NOT NULL,
    term       TEXT NOT NULL,
    meaning    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'auto',
    confidence REAL NOT NULL DEFAULT 0.5,
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope, term)
);
CREATE TABLE IF NOT EXISTS learn_state (
    scope   TEXT PRIMARY KEY,
    last_ts REAL NOT NULL
);
"""


def enabled() -> bool:
    return (os.getenv("QQ_BOT_MEMORY", "1") or "1").strip().lower() not in (
        "0", "false", "off", "no", ""
    )


class MemoryStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = (
            db_path
            or (os.getenv("QQ_BOT_MEMORY_DB", "") or "").strip()
            or DB_PATH
        )
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        if not self._ready:
            conn.executescript(_SCHEMA)
            self._ready = True
        return conn

    # ---------------- 称呼 / 印象 ----------------

    async def set_alias(self, scope: str, openid: str, alias: str) -> None:
        if not enabled():
            return
        await asyncio.to_thread(self._set_alias_sync, scope, openid, alias)

    def _set_alias_sync(self, scope: str, openid: str, alias: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO aliases(scope, openid, alias, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(scope, openid) DO UPDATE SET "
                "alias = excluded.alias, updated_at = excluded.updated_at",
                (scope, openid, (alias or "").strip()[:16], time.time()),
            )

    async def get_alias(self, scope: str, openid: str) -> str:
        if not enabled():
            return ""
        return await asyncio.to_thread(self._get_alias_sync, scope, openid)

    def _get_alias_sync(self, scope: str, openid: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT alias FROM aliases WHERE scope = ? AND openid = ?",
                (scope, openid),
            ).fetchone()
        return (row["alias"] or "") if row else ""

    async def set_impression(
        self, scope: str, openid: str, note: str, source: str = "manual"
    ) -> None:
        if not enabled():
            return
        await asyncio.to_thread(
            self._set_impression_sync, scope, openid, note, source
        )

    def _set_impression_sync(
        self, scope: str, openid: str, note: str, source: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO impressions(scope, openid, note, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(scope, openid) DO UPDATE SET "
                "note = excluded.note, source = excluded.source, "
                "updated_at = excluded.updated_at",
                (scope, openid, (note or "").strip()[:400], source, time.time()),
            )

    async def get_impression(self, scope: str, openid: str) -> str:
        if not enabled():
            return ""
        return await asyncio.to_thread(self._get_impression_sync, scope, openid)

    def _get_impression_sync(self, scope: str, openid: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT note FROM impressions WHERE scope = ? AND openid = ?",
                (scope, openid),
            ).fetchone()
        return (row["note"] or "") if row else ""

    async def clear_impressions(self, scope: str) -> int:
        return await asyncio.to_thread(self._clear_impressions_sync, scope)

    def _clear_impressions_sync(self, scope: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM impressions WHERE scope = ?", (scope,))
        return cur.rowcount or 0

    # ---------------- 黑话 ----------------

    async def add_slang(
        self,
        scope: str,
        term: str,
        meaning: str,
        source: str = "manual",
        confidence: float = 1.0,
    ) -> bool:
        if not enabled():
            return False
        return await asyncio.to_thread(
            self._add_slang_sync, scope, term, meaning, source, confidence
        )

    def _add_slang_sync(
        self, scope: str, term: str, meaning: str, source: str, confidence: float
    ) -> bool:
        term = (term or "").strip()[:24]
        meaning = (meaning or "").strip()[:120]
        if not term or not meaning:
            return False
        with self._connect() as conn:
            old = conn.execute(
                "SELECT source FROM slang WHERE scope = ? AND term = ?",
                (scope, term),
            ).fetchone()
            # 自动学习不覆盖管理员手写的条目
            if old and old["source"] == "manual" and source == "auto":
                return False
            conn.execute(
                "INSERT INTO slang(scope, term, meaning, source, confidence, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(scope, term) DO UPDATE SET "
                "meaning = excluded.meaning, source = excluded.source, "
                "confidence = excluded.confidence, updated_at = excluded.updated_at",
                (
                    scope,
                    term,
                    meaning,
                    source,
                    max(0.0, min(1.0, float(confidence))),
                    time.time(),
                ),
            )
        return True

    async def get_slang(self, scope: str, term: str) -> dict | None:
        return await asyncio.to_thread(self._get_slang_sync, scope, term)

    def _get_slang_sync(self, scope: str, term: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT term, meaning, source, confidence FROM slang "
                "WHERE scope = ? AND term = ?",
                (scope, (term or "").strip()),
            ).fetchone()
        return dict(row) if row else None

    async def list_slang(self, scope: str, limit: int = 50) -> list[dict]:
        return await asyncio.to_thread(self._list_slang_sync, scope, limit)

    def _list_slang_sync(self, scope: str, limit: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT term, meaning, source, confidence FROM slang "
                "WHERE scope = ? ORDER BY updated_at DESC LIMIT ?",
                (scope, max(1, min(100, limit))),
            ).fetchall()
        return [dict(r) for r in rows]

    async def delete_slang(self, scope: str, term: str) -> bool:
        return await asyncio.to_thread(self._delete_slang_sync, scope, term)

    def _delete_slang_sync(self, scope: str, term: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM slang WHERE scope = ? AND term = ?",
                (scope, (term or "").strip()),
            )
        return bool(cur.rowcount)

    async def find_slang(self, scope: str, text: str, limit: int = 5) -> list[dict]:
        return await asyncio.to_thread(self._find_slang_sync, scope, text, limit)

    def _find_slang_sync(self, scope: str, text: str, limit: int) -> list[dict]:
        hay = (text or "").lower()
        if not hay:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT term, meaning, source, confidence FROM slang "
                "WHERE scope = ? AND confidence >= 0.5",
                (scope,),
            ).fetchall()
        hits = [
            dict(r)
            for r in rows
            if (len(r["term"]) >= 2 or r["source"] == "manual")
            and r["term"].lower() in hay
        ]
        hits.sort(key=lambda r: len(r["term"]), reverse=True)
        return hits[: max(1, min(10, limit))]

    # ---------------- 自动学习状态 ----------------

    async def get_last_learn(self, scope: str) -> float:
        return await asyncio.to_thread(self._get_last_learn_sync, scope)

    def _get_last_learn_sync(self, scope: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_ts FROM learn_state WHERE scope = ?", (scope,)
            ).fetchone()
        return float(row["last_ts"]) if row else 0.0

    async def set_last_learn(self, scope: str, ts: float) -> None:
        await asyncio.to_thread(self._set_last_learn_sync, scope, ts)

    def _set_last_learn_sync(self, scope: str, ts: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO learn_state(scope, last_ts) VALUES (?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET last_ts = excluded.last_ts",
                (scope, float(ts)),
            )

    # ---------------- 注入块 ----------------

    async def render(
        self, scope: str, openid: str, text: str, context_text: str = ""
    ) -> str:
        if not enabled():
            return ""
        return await asyncio.to_thread(
            self._render_sync, scope, openid, text, context_text
        )

    async def render_profiles(
        self, scope: str, members: list[dict], limit: int = 5
    ) -> str:
        """
        群友画像：给最近活跃成员拼一段「谁是谁、什么脾气」。

        只输出有过的称呼/印象，没印象的人不硬编；随机接话时用它来决定
        接谁的话、用什么口气。数据来自自动学习，`/印象` 也能人工修正。
        """
        if not enabled() or not members:
            return ""
        return await asyncio.to_thread(self._render_profiles_sync, scope, members, limit)

    def _render_profiles_sync(
        self, scope: str, members: list[dict], limit: int
    ) -> str:
        openids = [
            (m.get("openid") or "").strip()
            for m in members[: max(1, min(12, limit * 3))]
            if (m.get("openid") or "").strip()
        ]
        if not openids:
            return ""
        marks = ",".join("?" for _ in openids)
        with self._connect() as conn:
            alias_rows = conn.execute(
                f"SELECT openid, alias FROM aliases WHERE scope = ? AND openid IN ({marks})",
                (scope, *openids),
            ).fetchall()
            imp_rows = conn.execute(
                f"SELECT openid, note FROM impressions WHERE scope = ? AND openid IN ({marks})",
                (scope, *openids),
            ).fetchall()
        aliases = {r["openid"]: (r["alias"] or "").strip() for r in alias_rows}
        notes = {r["openid"]: (r["note"] or "").strip() for r in imp_rows}

        lines: list[str] = []
        for m in members:
            oid = (m.get("openid") or "").strip()
            if not oid:
                continue
            name = aliases.get(oid) or (m.get("name") or "").strip()
            note = notes.get(oid, "")
            if not note:
                continue
            who = name or "某个群友"
            lines.append(f"{who}：{note}")
            if len(lines) >= limit:
                break
        if not lines:
            return ""
        return "【群友画像】\n" + "\n".join(lines) + "\n"

    def _render_sync(
        self, scope: str, openid: str, text: str, context_text: str
    ) -> str:
        with self._connect() as conn:
            alias_row = conn.execute(
                "SELECT alias FROM aliases WHERE scope = ? AND openid = ?",
                (scope, openid),
            ).fetchone()
            imp_row = conn.execute(
                "SELECT note FROM impressions WHERE scope = ? AND openid = ?",
                (scope, openid),
            ).fetchone()
        alias = (alias_row["alias"] or "") if alias_row else ""
        note = (imp_row["note"] or "") if imp_row else ""
        hits = self._find_slang_sync(scope, (text or "") + "\n" + (context_text or ""), 5)

        lines: list[str] = []
        if alias or note:
            who = alias or "这个发言人"
            if note:
                lines.append(f"（本机对{who}的印象：{note}）")
            else:
                lines.append(f"（本机管这个发言人叫「{who}」）")
        if hits:
            pairs = "；".join(f"{h['term']}={h['meaning']}" for h in hits)
            lines.append(f"（本群黑话：{pairs}）")
        return "\n".join(lines) + ("\n" if lines else "")
