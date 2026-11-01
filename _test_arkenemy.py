# -*- coding: utf-8 -*-
"""敌人档案快照测试（不联网）。"""

import gzip
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_arkenemy_")
PATH = os.path.join(TMP, "arkenemy.json.gz")
data = {
    "源石虫": [
        {
            "id": "enemy_1007_slime",
            "level": "NORMAL",
            "damage": ["PHYSIC"],
            "desc": "野生的被感染生物。这种低智能生物极有可能会被敌方术师操纵。",
        }
    ]
}
with gzip.open(PATH, "wt", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False)
os.environ["QQ_BOT_ARKENEMY"] = PATH

import arkenemy  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


def main() -> int:
    text = arkenemy.context("源石虫是什么敌人")
    check("命中敌人", "源石虫" in text and "被感染生物" in text, text)
    check("等级/伤害", "普通" in text and "物理" in text, text)
    check("闲聊不注入", arkenemy.context("源石虫好可爱") == "")
    check("未知敌人返回空", arkenemy.context("不存在怪物档案") == "")
    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
