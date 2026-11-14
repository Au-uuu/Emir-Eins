# -*- coding: utf-8 -*-
"""
模型用量账本：每次调用记 token / 费用，供 `/用量` 汇总。

表：`usage(ts, scope, model, kind, prompt, completion, reasoning, cached, cost)`
- `scope`：`group:<openid>` / `c2c:<openid>` / `memory`（学习/摘要）
- `kind`：`text` / `vision` / `learn` / `summary`
- `cost`：OpenCode 返回的 `list_cost`（美元）；其他厂商没有就记 0

配置：
  `QQ_BOT_USAGE`      1/0，默认开
  `QQ_BOT_USAGE_DB`   默认 data/usage.db
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time

log = logging.getLogger("qqbot.usage")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "usage.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    scope       TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'text',
    prompt      INTEGER NOT NULL DEFAULT 0,
    completion  INTEGER NOT NULL DEFAULT 0,
    reasoning   INTEGER NOT NULL DEFAULT 0,
    cached      INTEGER NOT NULL DEFAULT 0,
    cost        REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
"""


def enabled() -> bool:
    return (os.getenv("QQ_BOT_USAGE", "1") or "1").strip().lower() not in (
        "0", "false", "off", "no", ""
    )


class UsageStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = (
            db_path
            or (os.getenv("QQ_BOT_USAGE_DB", "") or "").strip()
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

    # ---------------- 写入（同步，在 persona 的线程里调用） ----------------

    def _direct_cost(self, usage: dict, model: str) -> float:
        """DeepSeek 官方 API 没给 list_cost 时，用公开单价估算（默认按峰时）。"""
        if "deepseek" not in (model or "").lower():
            return 0.0
        prompt = int(usage.get("prompt_tokens") or 0)
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        if "pro" in (model or "").lower():
            default_in, default_out, default_cache = 1.32, 3.96, 0.044
        else:
            default_in, default_out, default_cache = 0.30, 1.20, 0.006
        try:
            in_rate = float(os.getenv("QQ_BOT_TEXT_COST_INPUT", "") or default_in)
            out_rate = float(os.getenv("QQ_BOT_TEXT_COST_OUTPUT", "") or default_out)
            cache_rate = float(
                os.getenv("QQ_BOT_TEXT_COST_CACHE", "") or default_cache
            )
        except ValueError:
            in_rate, out_rate, cache_rate = default_in, default_out, default_cache
        return (
            max(0, prompt - cached) / 1_000_000 * in_rate
            + cached / 1_000_000 * cache_rate
            + completion / 1_000_000 * out_rate
        )

    def record(self, scope: str, model: str, kind: str, usage: dict) -> None:
        if not enabled() or not isinstance(usage, dict):
            return
        try:
            details = usage.get("prompt_tokens_details") or {}
            oc = usage.get("opencode") or {}
            ocd = oc.get("tokens") or {}
            reasoning = int(ocd.get("reasoning") or (
                (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
            ))
            cost = float(oc.get("list_cost") or 0.0)
            if cost <= 0:
                cost = self._direct_cost(usage, model)
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO usage(ts, scope, model, kind, prompt, completion, "
                    "reasoning, cached, cost) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        time.time(),
                        (scope or "")[:80],
                        (model or "")[:60],
                        (kind or "text")[:20],
                        int(usage.get("prompt_tokens") or 0),
                        int(usage.get("completion_tokens") or 0),
                        reasoning,
                        int(details.get("cached_tokens") or 0),
                        cost,
                    ),
                )
                # 低频清理：只留 90 天
                if int(time.time()) % 200 == 0:
                    conn.execute(
                        "DELETE FROM usage WHERE ts < ?", (time.time() - 90 * 86400,)
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning("[用量] 记录失败：%s", exc)

    # ---------------- 查询 ----------------

    async def summary(self, days: float = 1.0) -> dict:
        return await asyncio.to_thread(self._summary_sync, days)

    def _summary_sync(self, days: float) -> dict:
        cutoff = time.time() - max(0.1, float(days)) * 86400
        data = self._summary_between_sync(cutoff, time.time())
        data["days"] = days
        return data

    async def summary_between(self, start_ts: float, end_ts: float) -> dict:
        """指定时间区间（左闭右开）的汇总，日报用。"""
        return await asyncio.to_thread(
            self._summary_between_sync, float(start_ts), float(end_ts)
        )

    def _summary_between_sync(self, start_ts: float, end_ts: float) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt),0) AS prompt, "
                "COALESCE(SUM(completion),0) AS completion, "
                "COALESCE(SUM(reasoning),0) AS reasoning, "
                "COALESCE(SUM(cached),0) AS cached, "
                "COALESCE(SUM(cost),0) AS cost "
                "FROM usage WHERE ts >= ? AND ts < ?",
                (start_ts, end_ts),
            ).fetchone()
            kinds = conn.execute(
                "SELECT kind, COUNT(*) AS calls, COALESCE(SUM(cost),0) AS cost "
                "FROM usage WHERE ts >= ? AND ts < ? GROUP BY kind ORDER BY calls DESC",
                (start_ts, end_ts),
            ).fetchall()
            scopes = conn.execute(
                "SELECT scope, COUNT(*) AS calls, COALESCE(SUM(cost),0) AS cost "
                "FROM usage WHERE ts >= ? AND ts < ? "
                "GROUP BY scope ORDER BY cost DESC, calls DESC LIMIT 8",
                (start_ts, end_ts),
            ).fetchall()
        return {
            "start": start_ts,
            "end": end_ts,
            "total": dict(total) if total else {},
            "kinds": [dict(r) for r in kinds],
            "scopes": [dict(r) for r in scopes],
        }


_default_store: UsageStore | None = None


def record(scope: str, model: str, kind: str, usage: dict) -> None:
    """persona 在线程里调用的模块级入口（惰性建单例，等 load_dotenv 之后）。"""
    global _default_store
    if not enabled():
        return
    if _default_store is None:
        _default_store = UsageStore()
    _default_store.record(scope, model, kind, usage)
