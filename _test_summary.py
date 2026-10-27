# -*- coding: utf-8 -*-
"""早期对话摘要：每 N 条压缩成 3~5 条事实，最近 M 条保留原文。"""

import asyncio
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_summary_")
os.environ["QQ_BOT_MEMORY"] = "0"
os.environ["QQ_BOT_GROUP_CONTEXT"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MIN"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MAX"] = "0"
os.environ["QQ_BOT_CHAT_SPLIT"] = "0"
os.environ["QQ_BOT_WEBSEARCH"] = "0"
os.environ["QQ_BOT_KB_DIR"] = os.path.join(TMP, "kb")

import bot  # noqa: E402
from chat_store import ChatStore  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


async def main() -> int:
    store = ChatStore(
        db_path=os.path.join(TMP, "chat.db"),
        max_messages=200,
        memory_minutes=60,
        recent_messages=20,
        summary_every=10,
    )
    scope = "group:S"

    print("[1] 触发条件")
    for i in range(15):
        await store.append(scope, f"u{i}", f"a{i}")
    check("30 条时触发（早期 10 条）", await store.should_summarize(scope) is True)
    check("历史只保留最近 20 条", len(await store.history(scope)) == 20, len(await store.history(scope)))

    print("[2] 摘要输入排除最近 20 条")
    old, covered, msgs = await store.summary_input(scope)
    check("共 30 条，摘要输入 10 条", len(msgs) == 10, len(msgs))
    check("摘要输入不含最近 20 条", all("u14" not in m["content"] for m in msgs), msgs)
    check("covered_id 有效", covered > 0, covered)

    print("[3] 写入摘要后不再重复")
    await store.set_summary(scope, "用户喜欢终末地", covered)
    check("摘要读回", await store.summary(scope) == "用户喜欢终末地")
    check("刚写完不触发", await store.should_summarize(scope) is False)
    check("历史仍只 20 条", len(await store.history(scope)) == 20)

    print("[4] 再积累 10 条才触发")
    for i in range(15, 20):
        await store.append(scope, f"u{i}", f"a{i}")
    check("新增 10 条触发", await store.should_summarize(scope) is True)
    old2, covered2, msgs2 = await store.summary_input(scope)
    check("旧摘要带出", old2 == "用户喜欢终末地", old2)
    check("covered 前进", covered2 > covered, (covered, covered2))
    check("不包含最近 20 条", all("u10" not in m["content"] and "u14" not in m["content"] for m in msgs2), msgs2)

    print("[5] 摘要编排 + 3~5 条事实")
    fake = "1. 用户喜欢终末地\n2. 约定每天 20 点提醒\n3. 未完成：找初音的图\n4. 用户不喜欢被叫全名"

    async def fake_raw(prompt, text, session="memory-summary", **kwargs):
        return fake

    orig_chat = bot.chat
    orig_raw = bot.persona.raw_complete
    bot.chat = store
    bot.persona.raw_complete = fake_raw
    try:
        await bot._summarize_scope(scope)
        text = await store.summary(scope)
        check("摘要更新为 4 条事实", text.count("\n") == 3, text)
        check("保留事实内容", "终末地" in text and "20 点" in text and "初音" in text, text)
        check("去掉编号", not text.startswith("1."), text)
    finally:
        bot.chat = orig_chat
        bot.persona.raw_complete = orig_raw

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
