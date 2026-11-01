# -*- coding: utf-8 -*-
"""本地干员数值快照：回答明日方舟技能 SP/初动/持续等硬数据。

数据来源：PRTS / ArknightsGamedata 的离线快照（`data/arkdata.json.gz`）。
只负责“数值查表”，不负责人格与措辞；查不到就返回空串，交给联网搜索或
让模型直说没查到，绝不凭记忆编数字。
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading

log = logging.getLogger("qqbot.arkdata")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(BASE_DIR, "data", "arkdata.json.gz")

_data: dict[str, list[dict]] | None = None
_names: list[str] = []
_lock = threading.Lock()

# 只有问到“数值/技能”时才查表，避免“我喜欢塔露拉”也被塞一段技能说明
_INTENT_RE = re.compile(
    r"技能|sp|技力|回转|初动|持续|冷却|倍率|数值|面板|专三|专精|多少|多久|几秒|"
    r"攻击力|防御力|生命值|法抗|阻挡|再部署|部署费用",
    re.I,
)
_SKILL_NO_RE = re.compile(r"([123一二三])\s*技能")
_SKILL_NO = {"1": 1, "一": 1, "2": 2, "二": 2, "3": 3, "三": 3}


def _path() -> str:
    return (os.getenv("QQ_BOT_ARKDATA", "") or "").strip() or DEFAULT_PATH


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
            log.warning("[数值] 读取 %s 失败：%s", path, exc)
            data = {}
        _data = data if isinstance(data, dict) else {}
        _names = sorted(_data, key=len, reverse=True)
        log.info("[数值] 已加载 %d 个干员快照", len(_data))
        return _data


def _fmt_num(value) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(num)) if num == int(num) else f"{num:g}"


def context(text: str) -> str:
    """命中干员名 + 数值意图时返回可直接注入的事实块；否则空串。"""
    text = (text or "").strip()
    if not text or not _INTENT_RE.search(text):
        return ""
    data = _load()
    if not data:
        return ""
    # 长名字优先，避免「白」之类的短名误命中
    hits = [name for name in _names if len(name) >= 2 and name in text]
    if not hits:
        return ""
    m = _SKILL_NO_RE.search(text)
    skill_no = _SKILL_NO.get(m.group(1)) if m else None

    lines: list[str] = []
    for name in hits[:2]:
        for op in data.get(name, []):
            en = (op.get("en") or "").strip()
            head = f"{name}（{en}）" if en and en != name else name
            lines.append(head)
            for skill in op.get("skills") or []:
                if skill_no and int(skill.get("i") or 0) != skill_no:
                    continue
                parts = [f"{skill.get('i')}技能「{skill.get('name') or '?'}」"]
                if skill.get("spCost") is not None:
                    parts.append(f"SP {_fmt_num(skill['spCost'])}")
                if skill.get("initSp") is not None:
                    parts.append(f"初始 SP {_fmt_num(skill['initSp'])}")
                duration = skill.get("duration")
                if duration is not None:
                    if float(duration) < 0:
                        parts.append("瞬发/无持续")
                    else:
                        parts.append(f"持续 {_fmt_num(duration)} 秒")
                lines.append("；".join(parts))
    if not lines:
        return ""
    return "【角色数值（PRTS 快照）】\n" + "\n".join(lines) + "\n"
