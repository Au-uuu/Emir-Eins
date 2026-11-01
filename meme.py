# -*- coding: utf-8 -*-
"""独立梗库：联网查梗、从对话里学梗，按词条存全局 SQLite。

和 `memory_store.slang` 的区别
------------------------------
- `slang` 是**本群黑话**，按群隔离，主要从群聊上下文推断；
- `meme` 是**全局梗库**，跨群共享，来源可以是联网检索、对话里别人解释、
  或管理员手写。查不到就明说，不拿旧知识硬编。

自动学习有两条路：
1. 聊天里出现「X 是什么梗 / 什么意思」→ 现有联网搜索拿到摘要后，
   `learn_from_summary()` 再抽成结构化词条存下来；
2. 后台学本群黑话时，对拿不准的新词调用 `research()` 联网核实。

配置
----
| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_BOT_MEME` | `1` | `0` 关闭梗库 |
| `QQ_BOT_MEME_SEARCH` | `1` | 允许联网查梗 |
| `QQ_BOT_MEME_AUTO` | `1` | 后台学黑话时顺带联网核实 |
| `QQ_BOT_MEME_CONF` | `0.55` | 低于这个置信度不入库 |
| `QQ_BOT_MEME_DB` | `data/meme.db` | 数据库路径 |
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
import time

import persona
import sensitive
import websearch

log = logging.getLogger("qqbot.meme")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "meme.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memes (
    term       TEXT PRIMARY KEY,
    meaning    TEXT NOT NULL,
    origin     TEXT NOT NULL DEFAULT '',
    usage      TEXT NOT NULL DEFAULT '',
    aliases    TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'web',
    confidence REAL NOT NULL DEFAULT 0.5,
    verified   INTEGER NOT NULL DEFAULT 0,
    query      TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memes_updated ON memes(updated_at);
CREATE TABLE IF NOT EXISTS meme_rejects (
    term       TEXT PRIMARY KEY,
    reason     TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
"""

_EXTRACT_PROMPT = (
    "你是网络梗词典的编辑。根据下面的检索摘要和聊天语境，整理词条「{term}」。\n"
    "只输出严格 JSON，不要输出 JSON 以外的任何字：\n"
    '{{"term":"{term}","meaning":"一句话解释（2~80字）",'
    '"origin":"出处/来源（没有依据就空串，绝不编）",'
    '"usage":"使用场景/例句（没有依据就空串）",'
    '"aliases":["别名1","别名2"],"confidence":0.0}}\n'
    "要求：\n"
    "1. 只写摘要/语境里有依据的内容；不确定就留空，不要用旧知识补；\n"
    "2. meaning 必须是「这个词是什么意思」，不要写成新闻或评价；\n"
    "3. 如果它根本不是一个梗/圈内词，或摘要里查不到，输出 "
    '{{"term":"{term}","meaning":"","confidence":0}}；\n'
    "4. confidence 是你对含义的把握，0~1。"
)

_QUOTED_RE = re.compile(r"[「“\"']([^」”\"']{2,20})[」”\"']")
_TERM_BEFORE_CUE_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9]{2,16}?)\s*(?:这个|那个)?\s*"
    r"(?:是什么梗|是啥梗|什么梗|啥梗|什么意思|啥意思|什么鬼|"
    r"出处|出典|怎么来的|哪来的|什么来头)"
)
_TERM_AFTER_VERB_RE = re.compile(
    r"(?:查|搜|科普|解释)(?:一下|下)?\s*"
    r"([\u4e00-\u9fa5A-Za-z0-9]{2,16})"
)
_CUE_RE = re.compile(
    r"什么梗|啥梗|什么意思|啥意思|什么鬼|出处|出典|怎么来的|哪来的|"
    r"没听过|看不懂|是什么意思|新梗|热梗|黑话|缩写|"
    r"查一下|查查|搜一下|搜搜|科普|解释一下"
)

_STOP_TERMS = {
    "你", "我", "他", "她", "它", "这", "那", "这个", "那个", "什么", "啥",
    "意思", "梗", "出处", "黑话", "缩写", "一下", "请问", "谁", "哪", "哪个",
    "怎么", "为什么", "是不是", "有没有", "可以", "帮我", "查查", "搜搜",
    "这是", "那是", "就是", "还是", "也是", "真是", "这是", "不是",
}

_lock = threading.Lock()
_cache: dict[str, dict] = {}
_cache_ready = False
_cooldown: dict[str, float] = {}
_research_lock = threading.Lock()
_last_research = 0.0


def enabled() -> bool:
    return (os.getenv("QQ_BOT_MEME", "1") or "1").strip().lower() not in (
        "0", "false", "off", "no", "",
    )


def search_enabled() -> bool:
    if not enabled():
        return False
    return (os.getenv("QQ_BOT_MEME_SEARCH", "1") or "1").strip().lower() not in (
        "0", "false", "off", "no", "",
    )


def auto_enabled() -> bool:
    if not search_enabled():
        return False
    return (os.getenv("QQ_BOT_MEME_AUTO", "1") or "1").strip().lower() not in (
        "0", "false", "off", "no", "",
    )


def _threshold() -> float:
    try:
        return float((os.getenv("QQ_BOT_MEME_CONF", "") or "0.55").strip())
    except ValueError:
        return 0.55


def _db_path() -> str:
    return (
        (os.getenv("QQ_BOT_MEME_DB", "") or "").strip()
        or DB_PATH
    )


def _connect() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _norm(term: str) -> str:
    term = " ".join(str(term or "").strip().split())
    return term.strip("「」“”\"'")[:20]


def valid_term(term: str) -> bool:
    term = _norm(term)
    if len(term) < 2 or term in _STOP_TERMS:
        return False
    if re.fullmatch(r"[\d\W_]+", term):
        return False
    return True


def question_term(text: str) -> str:
    """从一句提问里猜要查的梗；猜不到返回空串。"""
    text = (text or "").strip()
    if not text or not _CUE_RE.search(text):
        return ""
    for match in _QUOTED_RE.finditer(text):
        term = _norm(match.group(1))
        if valid_term(term):
            return term
    for pattern in (_TERM_BEFORE_CUE_RE, _TERM_AFTER_VERB_RE):
        match = pattern.search(text)
        if match:
            term = _norm(match.group(1))
            if valid_term(term):
                return term
    # 整句很短、去掉提示词后只剩一个词，也可以当词条
    stripped = re.sub(
        r"什么梗|啥梗|什么意思|啥意思|什么鬼|出处|出典|怎么来的|哪来的|"
        r"没听过|看不懂|新梗|热梗|黑话|缩写|请问|帮我|一下|这个|那个|是|的",
        " ", text,
    )
    term = _norm(stripped)
    if valid_term(term) and len(term) <= 12 and " " not in term:
        return term
    return ""


def _load_cache() -> dict[str, dict]:
    global _cache, _cache_ready
    if _cache_ready:
        return _cache
    with _lock:
        if _cache_ready:
            return _cache
        cache: dict[str, dict] = {}
        try:
            with _connect() as conn:
                for row in conn.execute("SELECT * FROM memes"):
                    item = dict(row)
                    cache[item["term"]] = item
        except sqlite3.Error as exc:
            log.warning("[梗库] 读取失败：%s", exc)
        _cache = cache
        _cache_ready = True
        return _cache


def _reset_cache() -> None:
    global _cache_ready
    with _lock:
        _cache_ready = False


def _aliases(item: dict) -> list[str]:
    raw = item.get("aliases") or ""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [x.strip() for x in re.split(r"[,，、;；]", str(raw)) if x.strip()]


def find_in_text(text: str, limit: int = 3) -> list[dict]:
    hay = (text or "").lower()
    if not hay:
        return []
    hits: list[tuple[int, dict]] = []
    for item in _load_cache().values():
        terms = [item.get("term") or ""] + _aliases(item)
        for term in terms:
            term = (term or "").strip()
            if len(term) >= 2 and term.lower() in hay:
                hits.append((len(term), item))
                break
    hits.sort(key=lambda pair: pair[0], reverse=True)
    out: list[dict] = []
    seen: set[str] = set()
    for _, item in hits:
        key = item.get("term") or ""
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max(1, min(5, limit)):
            break
    return out


def render(text: str, limit: int = 3) -> str:
    if not enabled():
        return ""
    hits = find_in_text(text, limit)
    if not hits:
        return ""
    lines = ["【梗库资料】（全局梗库，按需参考；不要说“梗库/检索”这类字眼）"]
    for item in hits:
        line = f"- {item['term']}：{item['meaning']}"
        origin = (item.get("origin") or "").strip()
        if origin:
            line += f"（出处：{origin}）"
        lines.append(line)
    return "\n".join(lines) + "\n"


async def get(term: str) -> dict | None:
    if not enabled():
        return None
    term = _norm(term)
    if not term:
        return None
    return await asyncio.to_thread(_get_sync, term)


def _get_sync(term: str) -> dict | None:
    return _load_cache().get(term)


async def has(term: str) -> bool:
    return await get(term) is not None


async def list_recent(limit: int = 20) -> list[dict]:
    if not enabled():
        return []
    return await asyncio.to_thread(_list_sync, limit)


def _list_sync(limit: int) -> list[dict]:
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memes ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(100, int(limit))),),
            ).fetchall()
    except sqlite3.Error as exc:
        log.warning("[梗库] 列表读取失败：%s", exc)
        return []
    return [dict(r) for r in rows]


async def add_manual(term: str, meaning: str, origin: str = "", usage: str = "") -> bool:
    if not enabled():
        return False
    term = _norm(term)
    meaning = " ".join((meaning or "").split())[:120]
    if not valid_term(term) or len(meaning) < 2:
        return False
    if sensitive.filter.hit(term) or sensitive.filter.hit(meaning):
        return False
    return await asyncio.to_thread(
        _upsert_sync, term, meaning, origin, usage, [], "manual", 1.0, "", 1
    )


async def delete(term: str) -> bool:
    if not enabled():
        return False
    term = _norm(term)
    if not term:
        return False
    return await asyncio.to_thread(_delete_sync, term)


def _delete_sync(term: str) -> bool:
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM memes WHERE term = ?", (term,))
    except sqlite3.Error as exc:
        log.warning("[梗库] 删除失败：%s", exc)
        return False
    _reset_cache()
    return bool(cur.rowcount)


REJECT_DAYS = 30.0


def _reject_sync(term: str, reason: str = "") -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO meme_rejects(term, reason, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(term) DO UPDATE SET "
                "reason = excluded.reason, updated_at = excluded.updated_at",
                (term, reason[:80], time.time()),
            )
    except sqlite3.Error as exc:
        log.warning("[梗库] 写入拒绝缓存失败：%s", exc)


def _is_rejected_sync(term: str, days: float = REJECT_DAYS) -> bool:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT updated_at FROM meme_rejects WHERE term = ?", (term,)
            ).fetchone()
    except sqlite3.Error:
        return False
    if not row:
        return False
    return time.time() - float(row["updated_at"] or 0) < days * 86400


async def is_rejected(term: str) -> bool:
    if not enabled():
        return False
    term = _norm(term)
    if not term:
        return False
    return await asyncio.to_thread(_is_rejected_sync, term)


def can_research(scope: str = "") -> bool:
    """给后台任务判断现在能不能发一次联网请求（避免排队撞限流）。"""
    return _cooldown_ok(scope)


def _upsert_sync(
    term: str,
    meaning: str,
    origin: str,
    usage: str,
    aliases: list[str],
    source: str,
    confidence: float,
    query: str,
    verified: int = 0,
) -> bool:
    now = time.time()
    try:
        with _connect() as conn:
            old = conn.execute("SELECT * FROM memes WHERE term = ?", (term,)).fetchone()
            if old is not None:
                old = dict(old)
                # 手写/已核验的条目不被自动结果覆盖
                if old.get("source") == "manual" or int(old.get("verified") or 0):
                    if source != "manual":
                        return False
            aliases_text = "，".join(dict.fromkeys(a.strip() for a in aliases if a.strip()))
            conn.execute(
                "INSERT INTO memes(term, meaning, origin, usage, aliases, source, "
                "confidence, verified, query, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(term) DO UPDATE SET "
                "meaning = excluded.meaning, origin = excluded.origin, "
                "usage = excluded.usage, aliases = excluded.aliases, "
                "source = excluded.source, confidence = excluded.confidence, "
                "verified = excluded.verified, query = excluded.query, "
                "updated_at = excluded.updated_at",
                (
                    term, meaning, origin, usage, aliases_text, source,
                    float(confidence), int(verified), query, now, now,
                ),
            )
            conn.execute("DELETE FROM meme_rejects WHERE term = ?", (term,))
    except sqlite3.Error as exc:
        log.warning("[梗库] 写入失败：%s", exc)
        return False
    _reset_cache()
    return True


def _parse_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except ValueError:
            return None
    return None


async def _extract(term: str, summary: str, hint: str = "") -> dict | None:
    if not summary:
        return None
    context = f"检索摘要：\n{summary}"
    if hint:
        context += f"\n\n聊天语境：\n{hint}"
    raw = await persona.raw_complete(
        _EXTRACT_PROMPT.format(term=term), context, session="meme", kind="learn"
    )
    data = _parse_json(raw or "")
    if not isinstance(data, dict):
        log.info("[梗库] 模型没有返回 JSON：%s", term)
        return None
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    meaning = " ".join(str(data.get("meaning") or "").split())[:120]
    if confidence < _threshold() or len(meaning) < 2:
        return None
    if sensitive.filter.hit(meaning) or sensitive.filter.hit(term):
        return None
    origin = " ".join(str(data.get("origin") or "").split())[:120]
    usage = " ".join(str(data.get("usage") or "").split())[:160]
    aliases = data.get("aliases") or []
    if not isinstance(aliases, list):
        aliases = []
    aliases = [
        _norm(a) for a in aliases
        if isinstance(a, str) and valid_term(_norm(a)) and _norm(a) != term
    ][:5]
    return {
        "term": term,
        "meaning": meaning,
        "origin": origin,
        "usage": usage,
        "aliases": aliases,
        "confidence": confidence,
    }


def _cooldown_ok(scope: str = "") -> bool:
    """全局 8 秒 + 单场景 20 秒，避免连点把搜索打爆。"""
    global _last_research
    now = time.time()
    with _lock:
        if now - _last_research < 8:
            return False
        if scope:
            last = _cooldown.get(scope, 0.0)
            if now - last < 20:
                return False
    return True


def _mark_cooldown(scope: str = "") -> None:
    global _last_research
    now = time.time()
    with _lock:
        _last_research = now
        if scope:
            _cooldown[scope] = now


async def research(term: str, hint: str = "", scope: str = "", force: bool = False) -> dict | None:
    """联网查一个梗并入库；本地已有且未 force 时直接返回。"""
    if not enabled():
        return None
    term = _norm(term)
    if not valid_term(term):
        return None
    local = await get(term)
    if local and not force:
        return local
    if not search_enabled() or not websearch.enabled():
        return local
    if not force and await is_rejected(term):
        return local
    if not _cooldown_ok(scope):
        log.info("[梗库] 触发限流，先返回本地结果：%s", term)
        return local

    query = f"{term} 是什么梗 出处 用法"
    if hint:
        query += f"（聊天语境：{hint[:60]}）"
    _mark_cooldown(scope)
    summary = await asyncio.to_thread(websearch.search, query)
    if not summary:
        log.info("[梗库] 联网没查到：%s", term)
        return local
    entry = await _extract(term, summary, hint)
    if not entry:
        await asyncio.to_thread(_reject_sync, term, "模型判定不是梗或置信度不足")
        return local
    ok = await asyncio.to_thread(
        _upsert_sync,
        term, entry["meaning"], entry["origin"], entry["usage"],
        entry["aliases"], "web", entry["confidence"], query,
    )
    if ok:
        log.info(
            "[梗库] 已入库 %s（%.2f）：%s",
            term, entry["confidence"], entry["meaning"][:40],
        )
        entry["source"] = "web"
        return entry
    return local


async def learn_from_summary(question: str, summary: str, scope: str = "") -> dict | None:
    """对话里已经联网搜过：把摘要整理成词条存下来，不重复搜索。"""
    if not enabled() or not summary:
        return None
    term = question_term(question)
    if not term:
        return None
    local = await get(term)
    if local:
        return local
    entry = await _extract(term, summary, question)
    if not entry:
        await asyncio.to_thread(_reject_sync, term, "对话摘要判定不是梗或置信度不足")
        return None
    ok = await asyncio.to_thread(
        _upsert_sync,
        term, entry["meaning"], entry["origin"], entry["usage"],
        entry["aliases"], "web", entry["confidence"], question[:120],
    )
    if ok:
        log.info("[梗库] 从对话学到 %s：%s", term, entry["meaning"][:40])
        return entry
    return None
