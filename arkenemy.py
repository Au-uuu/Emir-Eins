# -*- coding: utf-8 -*-
"""本地敌人档案快照（`data/arkenemy.json.gz`）。

按敌人名查基础档案/描述；只回答“这个敌人是什么”，不代替关卡攻略。
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading

log = logging.getLogger("qqbot.arkenemy")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(BASE_DIR, "data", "arkenemy.json.gz")

_data: dict[str, list[dict]] | None = None
_names: list[str] = []
_lock = threading.Lock()

_INTENT_RE = re.compile(r"敌人|怪物|敌方|敌人档案|什么怪|首领|boss|机制", re.I)
_LEVELS = {"NORMAL": "普通", "ELITE": "精英", "BOSS": "领袖"}
_DAMAGE = {"PHYSIC": "物理", "MAGIC": "法术", "NO_DAMAGE": "无伤害", "HEAL": "治疗"}


def _path() -> str:
    return (os.getenv("QQ_BOT_ARKENEMY", "") or "").strip() or DEFAULT_PATH


def _load() -> dict[str, list[dict]]:
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
            log.warning("[敌人] 读取 %s 失败：%s", path, exc)
            data = {}
        _data = data if isinstance(data, dict) else {}
        _names = sorted(_data, key=len, reverse=True)
        log.info("[敌人] 已加载 %d 个敌人档案", len(_data))
        return _data


def context(text: str) -> str:
    text = (text or "").strip()
    if not text or not _INTENT_RE.search(text):
        return ""
    data = _load()
    if not data:
        return ""
    hits = [name for name in _names if len(name) >= 2 and name in text]
    if not hits:
        return ""
    lines = [f"【敌人档案（本地快照）】{hits[0]}"]
    for item in data.get(hits[0], [])[:2]:
        level = _LEVELS.get(item.get("level") or "", item.get("level") or "")
        damage = "、".join(_DAMAGE.get(x, x) for x in item.get("damage") or [])
        meta = "；".join(x for x in (f"等级 {level}" if level else "", f"伤害类型 {damage}" if damage else "") if x)
        if meta:
            lines.append(meta)
        desc = (item.get("desc") or "").strip()
        if desc:
            lines.append(desc[:1000])
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n"
