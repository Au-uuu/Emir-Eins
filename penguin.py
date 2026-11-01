# -*- coding: utf-8 -*-
"""企鹅物流（penguin-stats.cn）材料掉率查询。

首次使用会生成 `data/material_drops.json.gz` 本地缓存；之后查询秒回。
缓存只保留每件材料效率最高的几个关卡，避免每次拉 2MB 原始 matrix。
数据来源企鹅物流公开 API，CC BY-NC 4.0。
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
import urllib.request

log = logging.getLogger("qqbot.penguin")

BASE = "https://penguin-stats.cn/PenguinStats/api/v2"
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "material_drops.json.gz")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_lock = threading.Lock()
_cache: dict | None = None

_INTENT_RE = re.compile(
    r"材料|掉落|掉率|刷|去哪|哪里|获取|样本|理智|土块",
    re.I,
)


def _get(path: str):
    req = urllib.request.Request(
        f"{BASE}/{path}",
        headers={"User-Agent": "qqbot-penguin/1.0", "Accept": "application/json"},
    )
    with _OPENER.open(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _build_cache() -> dict:
    items = _get("items")
    stages = _get("stages")
    matrix = (_get("result/matrix?server=CN") or {}).get("matrix") or []
    item_map = {x.get("itemId"): x.get("name") for x in items if isinstance(x, dict)}
    stage_map = {x.get("stageId"): x for x in stages if isinstance(x, dict)}
    by: dict[str, dict] = {}
    for row in matrix:
        name = item_map.get(row.get("itemId"))
        stage = stage_map.get(row.get("stageId"))
        if not name or not stage:
            continue
        try:
            times = float(row.get("times") or 0)
            qty = float(row.get("quantity") or 0)
            ap = float(stage.get("apCost") or 0)
        except (TypeError, ValueError):
            continue
        if times <= 0 or qty <= 0 or ap <= 0:
            continue
        rate = qty / times
        sanity = ap / rate if rate > 0 else 0
        slot = by.setdefault(name, {})
        # 按关卡代号去重：不同 stageId 可能显示同一个 code（如活动复刻），
        # 展示时只认 code，留样本量最大的那条，避免同一关卡重复出现。
        code = stage.get("code") or stage.get("stageId")
        old = slot.get(code)
        if old is None or times > old["times"]:
            slot[code] = {
                "stage": code,
                "times": times,
                "rate": rate,
                "sanity": sanity,
            }
    out = {}
    for name, slot in by.items():
        rows = sorted(slot.values(), key=lambda x: x["sanity"])
        rows = [r for r in rows if r["times"] >= 50][:6] or rows[:6]
        out[name] = [
            {
                "stage": r["stage"],
                "rate": round(r["rate"] * 100, 1),
                "sanity": round(r["sanity"], 1),
                "times": int(r["times"]),
            }
            for r in rows
        ]
    data = {"source": "penguin-stats.cn", "items": out}
    try:
        os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
        with gzip.open(CACHE_PATH, "wt", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    except OSError as exc:
        log.warning("[材料] 写缓存失败：%s", exc)
    log.info("[材料] 已生成 %d 种材料掉率缓存", len(out))
    return data


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            with gzip.open(CACHE_PATH, "rt", encoding="utf-8") as fh:
                _cache = json.load(fh)
        except (OSError, ValueError):
            _cache = _build_cache()
        return _cache


def context(text: str) -> str:
    text = (text or "").strip()
    if not text or not _INTENT_RE.search(text):
        return ""
    data = _load()
    items = data.get("items") or {}
    if not items:
        return ""
    name = next((n for n in sorted(items, key=len, reverse=True) if n in text), None)
    if not name:
        return ""
    rows = items.get(name) or []
    if not rows:
        return f"【材料掉率（企鹅物流）】{name}\n没有查到有效掉落样本。"
    lines = [f"【材料掉率（企鹅物流 CN）】{name}"]
    for r in rows[:5]:
        lines.append(
            f"{r['stage']}：掉率 {r['rate']:.1f}%，"
            f"约 {r['sanity']:.1f} 理智/个（样本 {r['times']:,}）"
        )
    lines.append("来源：企鹅物流 penguin-stats.cn；样本量小的关卡仅供参考。")
    return "\n".join(lines) + "\n"
