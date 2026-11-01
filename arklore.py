# -*- coding: utf-8 -*-
"""本地干员语音/档案/设定快照。

数据来源：PRTS / ArknightsGamedata 的离线快照（`data/arklore.json.gz`）。
按需注入，不整本塞进 KB：问到某位干员的语音/档案时，只取该干员的一小段。
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading

log = logging.getLogger("qqbot.arklore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(BASE_DIR, "data", "arklore.json.gz")

_data: dict[str, dict] | None = None
_names: list[str] = []
_lock = threading.Lock()

_LORE_RE = re.compile(r"语音|台词|说了什么|档案|资料|设定|故事|背景|身高|生日|阵营|经历|记录|档案", re.I)
_VOICE_RE = re.compile(r"语音|台词|说了什么|voice", re.I)
_ARCHIVE_RE = re.compile(r"档案|资料|设定|故事|背景|身高|生日|阵营|经历|记录", re.I)

VOICE_LIMIT = 8
ARCHIVE_CHARS = 1600


def _path() -> str:
    return (os.getenv("QQ_BOT_ARKLORE", "") or "").strip() or DEFAULT_PATH


def _load() -> dict[str, dict]:
    global _data, _names
    if _data is not None:
        return _data
    with _lock:
        if _data is not None:
            return _data
        path = _path()
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            log.warning("[资料] 读取 %s 失败：%s", path, exc)
            data = {}
        _data = data if isinstance(data, dict) else {}
        _names = sorted(_data, key=len, reverse=True)
        log.info("[资料] 已加载 %d 个干员语音/档案", len(_data))
        return _data


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "……"


def context(text: str) -> str:
    """命中干员名 + 语音/档案意图时，返回该干员的一小段资料。"""
    text = (text or "").strip()
    if not text or not _LORE_RE.search(text):
        return ""
    data = _load()
    if not data:
        return ""
    hits = [name for name in _names if len(name) >= 2 and name in text]
    if not hits:
        return ""
    name = hits[0]
    item = data.get(name) or {}
    want_voice = bool(_VOICE_RE.search(text))
    want_archive = bool(_ARCHIVE_RE.search(text))
    if not want_voice and not want_archive:
        return ""

    lines = [f"【角色资料（本地快照）】{name}"]
    if want_archive:
        archive = item.get("archive") or []
        for block in archive[:6]:
            title = (block.get("t") or "").strip()
            body = _clip(block.get("x") or "", ARCHIVE_CHARS)
            if body:
                lines.append(f"档案·{title or '资料'}：{body}")
            if sum(len(x) for x in lines) >= ARCHIVE_CHARS:
                break
    if want_voice:
        voices = item.get("voices") or []
        # 如果问的是“任命助理”这类具体标题，先按标题过滤
        keyword = ""
        m = re.search(r"语音[：: ]*([^\s，。]{2,8})", text)
        if m:
            keyword = m.group(1)
        chosen = [
            v for v in voices
            if not keyword or keyword in (v.get("t") or "")
        ][:VOICE_LIMIT]
        for v in chosen:
            title = (v.get("t") or "语音").strip()
            body = _clip(v.get("x") or "", 260)
            if body:
                lines.append(f"语音·{title}：{body}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n"
