# -*- coding: utf-8 -*-
"""从 ArknightsGamedataPure 离线表生成本机快照。

产物：
- `data/arkdata.json.gz`：干员面板关键帧、信赖、天赋、模组、技能。
- `data/arkenemy.json.gz`：敌人档案。
- `data/arkdata.meta.json`：数据来源 commit，供每日更新检查对比。

用法：
    python tools/build_arkdata.py                 # 自动下载最新表
    python tools/build_arkdata.py --src <目录>    # 用已下载的 json 表（文件名需与 excel/ 下一致）

快照只保留查询需要的字段，不打包整张 20MB 表。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import urllib.request

REPO = "closure-studio/ArknightsGamedataPure"
BRANCH = "main"
RAW = f"https://cdn.jsdelivr.net/gh/{REPO}@{BRANCH}/excel"

TABLES = {
    "character_table.json": "operators",
    "skill_table.json": "skills",
    "uniequip_table.json": "modules",
    "battle_equip_table.json": "module_battle",
    "enemy_handbook_table.json": "enemies",
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

CLASSES = {
    "WARRIOR": "近卫", "SNIPER": "狙击", "TANK": "重装", "MEDIC": "医疗",
    "SUPPORT": "辅助", "CASTER": "术士", "SPECIAL": "特种", "PIONEER": "先锋",
}
_PANEL_KEYS = (
    "maxHp", "atk", "def", "magicResistance", "cost", "blockCnt",
    "baseAttackTime", "respawnTime",
)
_MODULE_STAT_KEYS = {
    "max_hp": "max_hp", "atk": "atk", "def": "def",
    "magic_resistance": "magic_resistance", "attack_speed": "attack_speed",
    "block_cnt": "block_cnt", "cost": "cost", "respawn_time": "respawn_time",
}
_TAG_RE = re.compile(r"<[^>]+>")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)(?::([^}]*))?\}")


def clean(text) -> str:
    text = _TAG_RE.sub("", str(text or ""))
    return " ".join(text.split())


def num(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return value


def fetch_table(name: str, src_dir: str | None) -> dict:
    path = os.path.join(src_dir, name) if src_dir else ""
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    url = f"{RAW}/{name}"
    req = urllib.request.Request(url, headers={"User-Agent": "qqbot-arkdata-builder/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fill_blackboard(text: str, blackboard) -> str:
    """把 `{key:0%}` / `{key}` 替换成表里的数值。"""
    values = {
        b.get("key"): b.get("value")
        for b in (blackboard or [])
        if isinstance(b, dict) and b.get("key")
    }

    def repl(match: re.Match) -> str:
        key, fmt = match.group(1), match.group(2) or ""
        if key not in values:
            return match.group(0)
        value = values[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            return clean(value)
        if fmt.endswith("%"):
            return f"{number * 100:.0f}%"
        if "." in fmt:
            decimals = len(fmt.split(".", 1)[1].replace("%", "").split("#")[0]) or 1
            return f"{number:.{decimals}f}".rstrip("0").rstrip(".")
        return str(int(round(number))) if float(number).is_integer() else f"{number:g}"

    return _PLACEHOLDER_RE.sub(repl, text)


def module_effect(battle: dict, mid: str) -> str:
    """取模组最高等级的说明（特性覆盖/天赋强化），数值占位符已代入。"""
    entry = battle.get(mid) or {}
    phases = entry.get("phases") or []
    if not phases:
        return ""
    texts: list[str] = []
    for part in phases[-1].get("parts") or []:
        for key in ("overrideTraitDataBundle", "addOrOverrideTalentDataBundle"):
            bundle = part.get(key) or {}
            for cand in bundle.get("candidates") or []:
                if int(cand.get("requiredPotentialRank") or 0) != 0:
                    continue
                desc = cand.get("additionalDescription")
                if not desc:
                    continue
                filled = clean(fill_blackboard(str(desc), cand.get("blackboard")))
                if filled:
                    texts.append(filled)
    # 去重但保持顺序
    return "；".join(dict.fromkeys(texts))


def build_operators(chars: dict, skills: dict, uniequip: dict, battle: dict) -> dict:
    mods: dict[str, list[dict]] = {}
    for mid, meta in (uniequip.get("equipDict") or {}).items():
        cid = meta.get("charId") or ""
        if not cid:
            continue
        if (meta.get("typeName1") or "").upper() in ("ORIGINAL", "INITIAL"):
            continue
        if int(meta.get("unlockEvolvePhase") or 0) < 2:
            continue
        stats: dict[str, float] = {}
        entry = battle.get(mid) or {}
        phases = entry.get("phases") or []
        if phases:
            for item in phases[-1].get("attributeBlackboard") or []:
                key = item.get("key")
                if key in _MODULE_STAT_KEYS and item.get("value") is not None:
                    stats[_MODULE_STAT_KEYS[key]] = num(item.get("value"))
        mods.setdefault(cid, []).append({
            "name": clean(meta.get("uniEquipName")),
            "type": clean(meta.get("typeName1")),
            "phase": meta.get("unlockEvolvePhase"),
            "level": meta.get("unlockLevel"),
            "stats": stats,
            "effect": module_effect(battle, mid),
        })

    out: dict[str, list[dict]] = {}
    for key, char in chars.items():
        if not isinstance(char, dict) or char.get("isNotObtainable"):
            continue
        name = char.get("name") or ""
        if not name:
            continue

        phases = []
        for phase in char.get("phases") or []:
            frames = []
            for frame in phase.get("attributesKeyFrames") or []:
                data = frame.get("data") or {}
                frames.append({
                    "level": frame.get("level"),
                    "data": {
                        k: num(data.get(k)) for k in _PANEL_KEYS
                        if data.get(k) is not None
                    },
                })
            if frames:
                phases.append({"max": phase.get("maxLevel"), "frames": frames})

        favor = {}
        frames = char.get("favorKeyFrames") or []
        if frames:
            data = frames[-1].get("data") or {}
            favor = {
                k: num(data.get(k)) for k in ("maxHp", "atk", "def", "magicResistance")
                if data.get(k) is not None
            }

        talents = []
        for group in char.get("talents") or []:
            cands = group.get("candidates") or []
            if not cands:
                continue
            cands = sorted(
                cands,
                key=lambda c: (
                    (c.get("unlockCondition") or {}).get("phase", 0),
                    -int(c.get("requiredPotentialRank") or 0),
                ),
                reverse=True,
            )
            best = cands[0]
            talents.append({
                "name": clean(best.get("name")),
                "phase": (best.get("unlockCondition") or {}).get("phase", 0),
                "desc": clean(best.get("description")),
            })

        skill_rows = []
        for index, item in enumerate(char.get("skills") or [], 1):
            table = skills.get(item.get("skillId") or "")
            if not table:
                continue
            levels = table.get("levels") or []
            if not levels:
                continue
            level = levels[-1]
            sp = level.get("spData") or {}
            skill_rows.append({
                "i": index,
                "name": level.get("name") or table.get("skillName") or "",
                "spCost": sp.get("spCost"),
                "initSp": sp.get("initSp"),
                "duration": level.get("duration"),
            })

        if phases or skill_rows or talents or mods.get(key):
            out.setdefault(name, []).append({
                "id": key,
                "en": char.get("appellation") or "",
                "class": CLASSES.get(char.get("profession") or "", ""),
                "phases": phases,
                "favor": favor,
                "talents": talents,
                "modules": mods.get(key, []),
                "skills": skill_rows,
            })
    return out


def build_enemies(table: dict) -> dict:
    out: dict[str, list[dict]] = {}
    for key, enemy in (table.get("enemyData") or {}).items():
        name = clean(enemy.get("name"))
        if not name:
            continue
        out.setdefault(name, []).append({
            "id": key,
            "level": clean(enemy.get("enemyLevel")),
            "desc": clean(enemy.get("description"))[:1200],
            "damage": enemy.get("damageType") or [],
        })
    return out


def write_gz(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=9) as fh:
        fh.write(raw)
    print(f"  {os.path.basename(path)}: {len(raw):,} -> {os.path.getsize(path):,} bytes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", help="已下载 excel/*.json 的目录（不填则自动下载）")
    parser.add_argument("--out", default=DATA_DIR, help="输出目录，默认 qqbot/data")
    args = parser.parse_args()

    tables = {}
    for name, label in TABLES.items():
        print(f"读取 {name} ...")
        tables[label] = fetch_table(name, args.src)

    print("生成快照 ...")
    operators = build_operators(
        tables["operators"], tables["skills"], tables["modules"], tables["module_battle"]
    )
    enemies = build_enemies(tables["enemies"])
    write_gz(os.path.join(args.out, "arkdata.json.gz"), operators)
    write_gz(os.path.join(args.out, "arkenemy.json.gz"), enemies)

    meta_path = os.path.join(args.out, "arkdata.meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            meta = {}
    meta.setdefault("source", REPO)
    meta["operators"] = len(operators)
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    with_effect = sum(
        1 for rows in operators.values() for op in rows
        for m in op.get("modules") or [] if m.get("effect")
    )
    total_modules = sum(
        1 for rows in operators.values() for op in rows for _ in op.get("modules") or []
    )
    print(
        f"完成：{len(operators)} 个干员，{len(enemies)} 种敌人，"
        f"模组 {total_modules} 个（{with_effect} 个带效果说明）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
