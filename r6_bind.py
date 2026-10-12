"""R6 绑定：把用户的 openid 关联到一个 R6 昵称，之后 `/r6` 直接查。

存储 `data/r6bind.db`（小体积、低频、值得随数据一起备份）。

绑定是**覆盖式**的：重新绑定直接改旧记录，所以没有「解绑」指令；
`unbind()` 保留给运维/脚本用（清掉某个 openid）。

⚠️ 关于粒度：QQ 官方机器人拿不到 QQ 号，只能拿到 openid——**单聊是 user_openid、
群聊是 member_openid，两者不同**。所以绑定是**按场景**的：在单聊绑的不会在群里生效，
换个群也得重绑。这是平台限制，绕不过去。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time

log = logging.getLogger("qqbot.r6bind")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "r6bind.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS r6_bind (
    openid     TEXT NOT NULL PRIMARY KEY,
    platform   TEXT NOT NULL,
    name       TEXT NOT NULL,
    profile_id TEXT,
    updated_at REAL NOT NULL
);
"""


class R6BindStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------- 对外（异步） ----------------

    async def bind(self, openid: str, platform: str, name: str,
                   profile_id: str | None = None) -> None:
        await asyncio.to_thread(self._bind_sync, openid, platform, name, profile_id)

    async def get(self, openid: str) -> dict | None:
        return await asyncio.to_thread(self._get_sync, openid)

    async def unbind(self, openid: str) -> bool:
        return await asyncio.to_thread(self._unbind_sync, openid)

    # ---------------- 同步实现 ----------------

    def _bind_sync(self, openid: str, platform: str, name: str, profile_id: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO r6_bind(openid, platform, name, profile_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(openid) DO UPDATE SET platform = excluded.platform, "
                "name = excluded.name, profile_id = excluded.profile_id, "
                "updated_at = excluded.updated_at",
                (openid, platform, name, profile_id, time.time()),
            )

    def _get_sync(self, openid: str) -> dict | None:
        if not openid:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT platform, name, profile_id FROM r6_bind WHERE openid = ?",
                (openid,),
            ).fetchone()
        return dict(row) if row else None

    def _unbind_sync(self, openid: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM r6_bind WHERE openid = ?", (openid,))
            return cur.rowcount > 0
