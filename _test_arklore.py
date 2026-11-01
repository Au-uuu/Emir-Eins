# -*- coding: utf-8 -*-
"""干员语音/档案快照测试（不联网）。"""

import gzip
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_arklore_")
PATH = os.path.join(TMP, "arklore.json.gz")
data = {
    "塔露拉": {
        "voices": [
            {"t": "任命助理", "x": "啊……抱歉，我还不习惯用一只手处理文书。"},
            {"t": "交谈1", "x": "我当然相信罗德岛的义肢技术。"},
        ],
        "archive": [
            {"t": "基础档案", "x": "【代号】塔露拉\n【性别】女\n【出身地】乌萨斯"},
            {"t": "客观履历", "x": "感染者反抗组织“整合运动”的创始人。"},
        ],
    }
}
with gzip.open(PATH, "wt", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False)
os.environ["QQ_BOT_ARKLORE"] = PATH

import arklore  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


def main() -> int:
    print("[1] 语音")
    text = arklore.context("塔露拉语音")
    check("命中语音", "语音·任命助理" in text and "一只手" in text, text)
    check("不塞档案", "档案·基础档案" not in text, text)

    print("[2] 档案")
    text = arklore.context("塔露拉档案身高")
    check("命中档案", "档案·基础档案" in text and "乌萨斯" in text, text)
    check("不塞语音", "语音·任命助理" not in text, text)

    print("[3] 意图过滤")
    check("闲聊不注入", arklore.context("塔露拉好帅") == "")

    print("[4] 未收录")
    check("未知干员返回空", arklore.context("不存在干员语音") == "")

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
