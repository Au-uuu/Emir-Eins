"""
游戏公告推送的持久化状态（群开关 + 已见公告去重）。

职责：
  - 每个群对**每个游戏独立**的推送开关（默认关；群内用 `/公告 开|关` 维护）
  - 各游戏「已见公告」的 cid 去重表：轮询只对没见过的 cid 推送，
    进程重启后也不会把旧公告重推一遍

目录结构：
  data/push.db    SQLite

为什么单独一个库：images.db 是图库资产、chat.db 是可丢弃的临时记忆，
推送开关与已见记录属于第三类状态（低频、小体积、值得随数据一起备份），
混进哪个库都会让备份/清理策略变得别扭。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time

log = logging.getLogger("qqbot.push")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "push.db")

# 已见公告保留天数：列表页一轮只有一二十条，90 天足够覆盖「下线很久又冒头」的
# 场景，再久的老 cid 重推一次也无害（还会先过标题关键词过滤）。定期清掉防止
# 表无限增长。
SEEN_RETENTION_DAYS = 90

_SCHEMA = """
CREATE TABLE IF NOT EXISTS push_groups (
    group_openid TEXT   NOT NULL,
    game         TEXT   NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 0,
    updated_at   REAL   NOT NULL,
    PRIMARY KEY (group_openid, game)
);
CREATE TABLE IF NOT EXISTS seen_news (
    game TEXT NOT NULL,
    cid  TEXT NOT NULL,
    ts   REAL NOT NULL,
    PRIMARY KEY (game, cid)
);
"""


class PushStore:
    """公告推送开关与已见公告（每个群 × 每个游戏一条开关记录）。"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------- 对外（异步，沿用 chat_store 的 to_thread 风格） ----------------

    async def set_enabled(self, group_openid: str, game: str, enabled: bool) -> bool:
        """设置某群某游戏的开关。返回 True = 状态发生变化，False = 原本就是该状态。"""
        return await asyncio.to_thread(
            self._set_enabled_sync, group_openid, game, 1 if enabled else 0
        )

    async def enabled_games(self, group_openid: str) -> dict[str, bool]:
        """取该群各游戏的开关（未记录的游戏不在返回里，视为关）。"""
        return await asyncio.to_thread(self._enabled_games_sync, group_openid)

    async def enabled_groups(self, game: str) -> list[str]:
        """取开启了该游戏推送的全部群 openid（推送目标）。"""
        return await asyncio.to_thread(self._enabled_groups_sync, game)

    async def filter_unseen(self, game: str, cids: list[str]) -> list[str]:
        """从 cid 列表里筛出还没见过的（保持原顺序）。"""
        return await asyncio.to_thread(self._filter_unseen_sync, game, cids)

    async def mark_seen(self, game: str, cids: list[str]) -> None:
        """记录这些 cid 为已见（幂等），顺手清理超过保留期的旧记录。"""
        await asyncio.to_thread(self._mark_seen_sync, game, cids)

    # ---------------- 同步实现 ----------------

    def _set_enabled_sync(self, group_openid: str, game: str, flag: int) -> bool:
        with self._connect() as conn:
            old = conn.execute(
                "SELECT enabled FROM push_groups WHERE group_openid = ? AND game = ?",
                (group_openid, game),
            ).fetchone()
            conn.execute(
                "INSERT INTO push_groups(group_openid, game, enabled, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(group_openid, game) "
                "DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at",
                (group_openid, game, flag, time.time()),
            )
        return not old or int(old["enabled"]) != flag

    def _enabled_games_sync(self, group_openid: str) -> dict[str, bool]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT game, enabled FROM push_groups WHERE group_openid = ?",
                (group_openid,),
            ).fetchall()
        return {r["game"]: bool(r["enabled"]) for r in rows}

    def _enabled_groups_sync(self, game: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT group_openid FROM push_groups WHERE game = ? AND enabled = 1",
                (game,),
            ).fetchall()
        return [r["group_openid"] for r in rows]

    def _filter_unseen_sync(self, game: str, cids: list[str]) -> list[str]:
        if not cids:
            return []
        with self._connect() as conn:
            placeholders = ",".join("?" * len(cids))
            rows = conn.execute(
                f"SELECT cid FROM seen_news WHERE game = ? AND cid IN ({placeholders})",
                (game, *cids),
            ).fetchall()
        seen = {r["cid"] for r in rows}
        out, got = [], set()
        for c in cids:
            if c not in seen and c not in got:
                got.add(c)
                out.append(c)
        return out

    def _mark_seen_sync(self, game: str, cids: list[str]) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen_news(game, cid, ts) VALUES (?, ?, ?)",
                [(game, c, now) for c in cids],
            )
            conn.execute(
                "DELETE FROM seen_news WHERE ts < ?",
                (now - SEEN_RETENTION_DAYS * 86400,),
            )
