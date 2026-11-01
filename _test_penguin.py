# -*- coding: utf-8 -*-
"""企鹅物流材料掉率缓存测试（不联网，用固定样本）。"""

import gzip
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_penguin_")

import penguin  # noqa: E402

# 用固定数据替换网络请求
FIXTURE = {
    "items": [
        {"itemId": "30012", "name": "固源岩"},
        {"itemId": "30011", "name": "聚酸酯块"},
    ],
    "stages": [
        {"stageId": "main_01-07", "code": "1-7", "apCost": 6},
        {"stageId": "main_02-12", "code": "S2-12", "apCost": 15},
        # 两个 stageId 显示同一个 code，用来验证按代号去重
        {"stageId": "act_14-09_a", "code": "14-9", "apCost": 21},
        {"stageId": "act_14-09_b", "code": "14-9", "apCost": 21},
    ],
    "matrix": [
        # 1-7 固源岩：6 理智 / (5/4) = 4.8
        {"stageId": "main_01-07", "itemId": "30012", "times": 1000, "quantity": 1250},
        # S2-12：15 / (2/1) = 7.5
        {"stageId": "main_02-12", "itemId": "30012", "times": 100, "quantity": 200},
        # 同一 code 的两条数据，应只保留样本量大的（b）
        {"stageId": "act_14-09_a", "itemId": "30011", "times": 100, "quantity": 5},
        {"stageId": "act_14-09_b", "itemId": "30011", "times": 5000, "quantity": 240},
        # 样本量太小，应被 50 样本门槛过滤（这里仅一条，会兜底保留）
        {"stageId": "main_01-07", "itemId": "30011", "times": 10, "quantity": 1},
    ],
}


def fake_get(path):
    if path == "items":
        return FIXTURE["items"]
    if path == "stages":
        return FIXTURE["stages"]
    if path.startswith("result/matrix"):
        return {"matrix": FIXTURE["matrix"]}
    raise AssertionError(f"unexpected path {path}")


penguin.CACHE_PATH = os.path.join(TMP, "material_drops.json.gz")
penguin._get = fake_get

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


def main() -> int:
    data = penguin._build_cache()
    items = data.get("items") or {}

    check("生成固源岩", "固源岩" in items, list(items))
    check("生成聚酸酯块", "聚酸酯块" in items, list(items))

    rock = items.get("固源岩") or []
    check("固源岩按理智排序", bool(rock) and rock[0]["stage"] == "1-7", rock)
    check("1-7 掉率 125%", bool(rock) and abs(rock[0]["rate"] - 125.0) < 0.1, rock)
    check("1-7 约 4.8 理智", bool(rock) and abs(rock[0]["sanity"] - 4.8) < 0.1, rock)

    ester = items.get("聚酸酯块") or []
    codes = [r["stage"] for r in ester]
    check("同一 code 去重后 14-9 只出现一次", codes.count("14-9") <= 1, codes)
    dup = [r for r in ester if r["stage"] == "14-9"]
    check("保留样本量大的那条 14-9", bool(dup) and dup[0]["times"] == 5000, dup)

    # 缓存文件应可读回
    with gzip.open(penguin.CACHE_PATH, "rt", encoding="utf-8") as fh:
        saved = json.load(fh)
    check("缓存已落盘", (saved.get("items") or {}).get("固源岩") == rock, list(saved))

    # context 命中与不命中
    text = penguin.context("固源岩在哪刷")
    check("context 命中材料", "【材料掉率" in text and "1-7" in text, text)
    check("闲聊不注入", penguin.context("固源岩好可爱") == "")

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
