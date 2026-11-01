# -*- coding: utf-8 -*-
"""本地干员数值快照：技能、面板、天赋、模组。

数据来源：PRTS / ArknightsGamedata 的离线快照（`data/arkdata.json.gz`）。
面板按等级关键帧线性插值；默认「精二 90 级、满信赖、无潜能」。
查不到就返回空串，交给联网或让模型直说没查到，绝不凭记忆编数字。
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

_INTENT_RE = re.compile(
    r"技能|sp|技力|回转|初动|持续|冷却|倍率|数值|面板|专三|专精|多少|多久|几秒|"
    r"攻击力|防御力|生命值|生命|血量|法抗|阻挡|再部署|费用|攻击间隔|满级|精[一二三]|天赋|模组|模块",
    re.I,
)
_SKILL_NO_RE = re.compile(r"([123一二三])\s*技能")
_SKILL_NO = {"1": 1, "一": 1, "2": 2, "二": 2, "3": 3, "三": 3}
_PANEL_RE = re.compile(r"面板|攻击|防御|生命|血量|法抗|阻挡|费用|再部署|攻击间隔|满级|精[一二三]")
_SKILL_RE = re.compile(r"技能|sp|技力|回转|初动|持续|冷却|专三|专精", re.I)
_TALENT_RE = re.compile(r"天赋")
_MODULE_RE = re.compile(r"模组|模块")
_ELITE_RE = re.compile(r"精(?:英)?\s*([一二三012])\s*(\d{1,3})?")
_LEVEL_RE = re.compile(r"(\d{1,3})\s*级")
_ELITE_MAP = {"0": 0, "一": 1, "1": 1, "二": 2, "2": 2, "三": 3, "3": 3}
_TRUST_RE = re.compile(r"无信赖|0\s*信赖|不满信赖")


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


def _fmt_num(value, decimals: int = 0) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if decimals <= 0:
        return str(int(round(num)))
    return f"{num:.{decimals}f}".rstrip("0").rstrip(".")


def _level_request(text: str) -> tuple[int, int | None]:
    """返回 (精英阶段, 等级或 None)。默认精二 90。"""
    m = _ELITE_RE.search(text)
    if m:
        elite = _ELITE_MAP.get(m.group(1), 2)
        level = int(m.group(2)) if m.group(2) else None
        return elite, level
    m = _LEVEL_RE.search(text)
    if m:
        return 2, int(m.group(1))
    return 2, 90


def _panel_at(op: dict, elite: int, level: int | None, trust: int = 100) -> dict:
    phases = op.get("phases") or []
    if not phases:
        return {}
    idx = max(0, min(len(phases) - 1, elite))
    phase = phases[idx]
    frames = sorted(phase.get("frames") or [], key=lambda f: f.get("level") or 0)
    if not frames:
        return {}
    max_level = int(phase.get("max") or frames[-1].get("level") or 1)
    target = max(1, min(max_level, level or max_level))
    lo = frames[0]
    hi = frames[-1]
    for i in range(len(frames) - 1):
        if frames[i].get("level", 0) <= target <= frames[i + 1].get("level", 0):
            lo, hi = frames[i], frames[i + 1]
            break
    l0 = float(lo.get("level") or 1)
    l1 = float(hi.get("level") or l0)
    ratio = 0.0 if l1 <= l0 else (target - l0) / (l1 - l0)
    d0 = lo.get("data") or {}
    d1 = hi.get("data") or {}
    keys = ("maxHp", "atk", "def", "magicResistance", "cost", "blockCnt", "baseAttackTime", "respawnTime")
    out = {}
    for key in keys:
        if d0.get(key) is None and d1.get(key) is None:
            continue
        a = float(d0.get(key) or 0)
        b = float(d1.get(key) or a)
        out[key] = a + (b - a) * ratio
    favor = op.get("favor") or {}
    for key, value in favor.items():
        if key in out and value:
            out[key] = out[key] + float(value) * max(0, min(100, trust)) / 100.0
    return out


def _panel_line(op: dict, elite: int, level: int | None, trust: int) -> str:
    panel = _panel_at(op, elite, level, trust)
    labels = (
        ("maxHp", "生命", 0), ("atk", "攻击", 0), ("def", "防御", 0),
        ("magicResistance", "法抗", 0), ("blockCnt", "阻挡", 0),
        ("cost", "部署费用", 0), ("baseAttackTime", "攻击间隔", 1),
        ("respawnTime", "再部署", 0),
    )
    parts = []
    for key, label, decimals in labels:
        if panel.get(key) is None:
            continue
        suffix = " 秒" if key in ("baseAttackTime", "respawnTime") else ""
        parts.append(f"{label} {_fmt_num(panel[key], decimals)}{suffix}")
    if not parts:
        return ""
    phase = max(0, min(2, elite))
    lv = level or 90
    trust_text = "满信赖" if trust >= 100 else ("无信赖" if trust <= 0 else f"信赖{trust}")
    return f"精{['零', '一', '二', '三'][phase]} {lv} 级｜{trust_text}：" + "；".join(parts)


def _module_line(module: dict) -> str:
    name = (module.get("name") or "").strip()
    type_name = (module.get("type") or "").strip() or "模组"
    stats = module.get("stats") or {}
    labels = (
        ("max_hp", "生命"), ("atk", "攻击"), ("def", "防御"),
        ("magic_resistance", "法抗"), ("attack_speed", "攻速"),
        ("block_cnt", "阻挡"), ("cost", "费用"), ("respawn_time", "再部署"),
    )
    parts = [
        f"{label} {_fmt_num(stats[key])}"
        for key, label in labels if stats.get(key) is not None
    ]
    suffix = "；" + "；".join(parts) if parts else ""
    effect = (module.get("effect") or "").strip()
    if effect:
        suffix += f"；效果：{effect[:300]}"
    return f"模组·{name}（{type_name}）{suffix}"


def context(text: str) -> str:
    """命中干员名 + 数值意图时返回可直接注入的事实块；否则空串。"""
    text = (text or "").strip()
    if not text or not _INTENT_RE.search(text):
        return ""
    data = _load()
    if not data:
        return ""
    hits = [name for name in _names if len(name) >= 2 and name in text]
    if not hits:
        return ""
    m = _SKILL_NO_RE.search(text)
    skill_no = _SKILL_NO.get(m.group(1)) if m else None
    want_panel = bool(_PANEL_RE.search(text))
    want_skill = bool(_SKILL_RE.search(text) or skill_no)
    want_talent = bool(_TALENT_RE.search(text))
    want_module = bool(_MODULE_RE.search(text))
    elite, level = _level_request(text)
    trust = 0 if _TRUST_RE.search(text) else 100

    lines: list[str] = []
    for name in hits[:2]:
        for op in data.get(name, []):
            en = (op.get("en") or "").strip()
            cls = (op.get("class") or "").strip()
            head = f"{name}（{en}）" if en and en != name else name
            if cls:
                head += f"·{cls}"
            lines.append(head)
            if want_panel:
                line = _panel_line(op, elite, level, trust)
                if line:
                    lines.append(line)
            if want_talent:
                for talent in op.get("talents") or []:
                    name_t = (talent.get("name") or "").strip()
                    desc_t = (talent.get("desc") or "").strip()
                    if name_t:
                        lines.append(f"天赋·{name_t}：{desc_t}")
            if want_module:
                for module in op.get("modules") or []:
                    line = _module_line(module)
                    if line:
                        lines.append(line)
            if not want_skill:
                continue
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
    if len(lines) <= 1:
        return ""
    return "【角色数值（PRTS 快照）】\n" + "\n".join(lines) + "\n"
