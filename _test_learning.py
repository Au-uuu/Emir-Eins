# -*- coding: utf-8 -*-
"""自动学习测试：黑话提取、印象提取、手写条目保护。"""

import asyncio
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_learning_")
os.environ["QQ_BOT_MEMORY"] = "1"
os.environ["QQ_BOT_MEMORY_DB"] = os.path.join(TMP, "memory.db")
os.environ["QQ_BOT_GROUP_CONTEXT"] = "1"
os.environ["QQ_BOT_GROUP_CONTEXT_DB"] = os.path.join(TMP, "gc.db")

import group_context  # noqa: E402
import learning  # noqa: E402
import memory_store  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


async def main() -> int:
    gctx = group_context.GroupContextStore(
        db_path=os.path.join(TMP, "gc.db"), max_messages=50, minutes=120, max_chars=4000
    )
    mem = memory_store.MemoryStore(db_path=os.path.join(TMP, "memory.db"))

    rows = [
        ("u1", "小明", "这波 yyds"),
        ("u2", "小红", "草，真的假的"),
        ("u1", "小明", "我昨天抽到了"),
        ("u2", "小红", "喜欢熬夜看直播"),
        ("u1", "小明", "终末地新角色还行"),
        ("u2", "小红", "说话直别介意"),
    ]
    for i, (oid, name, text) in enumerate(rows):
        await gctx.remember("G", f"m{i}", oid, text, name)

    calls = []

    async def fake_raw(prompt, text, session="memory-learn"):
        calls.append((prompt[:20], session))
        if "黑话" in prompt:
            return json.dumps([
                {"term": "yyds", "meaning": "永远的神", "confidence": 0.9},
                {"term": "低置信", "meaning": "不该存", "confidence": 0.2},
            ], ensure_ascii=False)
        return json.dumps(["说话直", "常聊终末地"], ensure_ascii=False)

    orig_raw = learning.persona.raw_complete
    learning.persona.raw_complete = fake_raw
    try:
        print("[1] 黑话学习")
        added = await learning.learn_slang(gctx, mem, "G", limit=20)
        check("只存高置信条目", [x["term"] for x in added] == ["yyds"], added)
        check("黑话已入库", (await mem.get_slang("group:G", "yyds"))["meaning"] == "永远的神")
        check("低置信没入库", (await mem.get_slang("group:G", "低置信")) is None)

        print("[2] 手写条目保护")
        await mem.add_slang("group:G", "yyds", "管理员写的", "manual", 1.0)
        await learning.learn_slang(gctx, mem, "G", limit=20)
        check("自动不覆盖手写", (await mem.get_slang("group:G", "yyds"))["meaning"] == "管理员写的")

        print("[3] 人物印象")
        n = await learning.learn_impressions(gctx, mem, "G", max_users=2, min_messages=2)
        check("学到 2 个人", n == 2, n)
        note1 = await mem.get_impression("group:G", "u1")
        note2 = await mem.get_impression("group:G", "u2")
        check("u1 印象已存", "说话直" in note1, note1)
        check("u2 印象已存", "说话直" in note2 or "常聊终末地" in note2, note2)

        print("[4] 模型乱返回不炸")
        async def bad_raw(prompt, text, session="memory-learn"):
            return "这不是 JSON"
        learning.persona.raw_complete = bad_raw
        check("乱返回返回空", await learning.learn_slang(gctx, mem, "G", limit=20) == [])
    finally:
        learning.persona.raw_complete = orig_raw

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
