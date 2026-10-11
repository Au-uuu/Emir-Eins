"""Danbooru 标签 → 中文名 查表（可靠词库，避免模型临时乱翻）。

为什么
------
离线打标器输出的是 danbooru 标签（罗马音/英文），交给视觉模型翻译时经常按字面
硬翻——`curren_chan` 被翻成「当前酱」、`still_in_love` 被翻成「仍然相爱」。
这里查一份**社区维护的 Danbooru 标签中英对照表**（category 3=作品 / 4=角色），
把准确中文名直接拼进线索，模型照抄即可。

词库
----
`data/tag_zh.sqlite`，来自
https://github.com/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table
（每日更新，字段 name / category / cn_name / post_count；MIT）。
该文件不进 git、不上传服务器代码包，需单独放到 data/ 下（与图片库同目录）。
缺文件时静默降级（不做中文对照，不影响打标与看图）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading

log = logging.getLogger("qqbot.names")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, "data", "tag_zh.sqlite")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_failed = False


def db_path() -> str:
    return os.getenv("QQ_BOT_TAG_ZH_DB", "") or DEFAULT_DB


def _connect() -> sqlite3.Connection | None:
    global _conn, _failed
    if _conn is not None or _failed:
        return _conn
    with _lock:
        if _conn is not None or _failed:
            return _conn
        path = db_path()
        if not os.path.isfile(path):
            _failed = True
            log.info("标签译名词库缺失（%s），角色/作品名不做中文对照", path)
            return None
        try:
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            _conn = conn
            n = conn.execute("select count(*) from tags").fetchone()[0]
            log.info("标签译名词库已就绪：%s（%d 条）", path, n)
        except Exception as exc:  # noqa: BLE001
            _failed = True
            log.warning("打开标签译名词库失败，已禁用：%s", exc)
            return None
    return _conn


def cn_name(tag: str) -> str | None:
    """查标签的准确中文名；无词库 / 查不到返回 None。"""
    conn = _connect()
    if conn is None or not tag:
        return None
    try:
        with _lock:
            row = conn.execute(
                "select cn_name from tags where name = ?", (tag,)
            ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    val = (row["cn_name"] or "").strip()
    return val or None
