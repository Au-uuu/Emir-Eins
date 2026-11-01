# -*- coding: utf-8 -*-
"""本地干员数值快照测试（不联网）。"""

import gzip
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_arkdata_")
PATH = os.path.join(TMP, "arkdata.json.gz")
data = {
    "塔露拉": [
        {
            "id": "char_4247_oxi",
            "en": "Talulah",
            "class": "近卫",
            "panel": {"maxHp": 2910, "atk": 875, "def": 425, "magicResistance": 10, "blockCnt": 2, "cost": 23, "baseAttackTime": 1.2, "respawnTime": 70},
            "skills": [
                {"i": 1, "name": "照霎白夜", "spCost": 8, "initSp": 0, "duration": -1},
                {"i": 2, "name": "阂断骤雪", "spCost": 7, "initSp": 21, "duration": 0},
                {"i": 3, "name": "日冕重烈", "spCost": 45, "initSp": 38, "duration": 36},
            ],
        }
    ],
    "阿米娅": [
        {
            "id": "char_002_amiya",
            "en": "Amiya",
            "skills": [
                {"i": 3, "name": "沸腾爆裂", "spCost": 120, "initSp": 0, "duration": 30},
            ],
        }
    ],
}
with gzip.open(PATH, "wt", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False)

os.environ["QQ_BOT_ARKDATA"] = PATH

import arkdata  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


def main() -> int:
    print("[1] 技能数值")
    text = arkdata.context("塔露拉三技能回转多少，持续多久")
    check("命中塔露拉", "塔露拉" in text, text)
    check("只给三技能", "日冕重烈" in text and "照霎白夜" not in text, text)
    check("SP 45", "SP 45" in text, text)
    check("初始 SP 38", "初始 SP 38" in text, text)
    check("持续 36", "持续 36" in text, text)

    text = arkdata.context("塔露拉攻击力防御阻挡")
    check("面板命中", "攻击 875" in text and "防御 425" in text and "阻挡 2" in text, text)

    print("[2] 意图过滤")
    check("闲聊不注入", arkdata.context("塔露拉好帅") == "")

    print("[3] 其他干员")
    text = arkdata.context("阿米娅三技能初动多少")
    check("阿米娅数值", "阿米娅" in text and "SP 120" in text and "持续 30" in text, text)

    print("[4] 查不到")
    check("未收录返回空", arkdata.context("不存在干员三技能多少") == "")

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
