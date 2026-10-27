# -*- coding: utf-8 -*-
"""记忆库测试：称呼 / 黑话 / 印象 / 命令。"""

import asyncio
import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_memory_")
os.environ["QQ_BOT_MEMORY"] = "1"
os.environ["QQ_BOT_MEMORY_DB"] = os.path.join(TMP, "memory.db")
os.environ["QQ_BOT_GROUP_CONTEXT"] = "0"
os.environ["QQ_BOT_GROUP_CONTEXT_DB"] = os.path.join(TMP, "gc.db")
os.environ["QQ_BOT_CHAT_DELAY_MIN"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MAX"] = "0"
os.environ["QQ_BOT_CHAT_SPLIT"] = "0"
os.environ["QQ_BOT_WEBSEARCH"] = "0"
os.environ["QQ_BOT_KB_DIR"] = os.path.join(TMP, "kb")

import bot  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    _seq = 0

    def __init__(self, content=""):
        FakeMessage._seq += 1
        self.id = f"mem{FakeMessage._seq}"
        self.content = content
        self.group_openid = "G"
        self.author = types.SimpleNamespace(member_openid="u1", user_openid="u1")
        self.attachments = []
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}

    @property
    def text(self):
        return "\n".join(r.get("content") or "" for r in self.replies)


async def main() -> int:
    mem = bot.memory
    scope = "group:G"

    print("[1] 称呼 / 印象")
    await mem.set_alias(scope, "u1", "小明")
    check("称呼读回", await mem.get_alias(scope, "u1") == "小明")
    await mem.set_impression(scope, "u1", "说话直，常聊终末地", "auto")
    block = await mem.render(scope, "u1", "今天终末地更新了吗")
    check("注入块含称呼", "小明" in block, block)
    check("注入块含印象", "常聊终末地" in block, block)

    print("[2] 黑话")
    await mem.add_slang(scope, "yyds", "永远的神", "manual", 1.0)
    await mem.add_slang(scope, "草", "语气词，表示无语或惊讶", "manual", 0.9)
    await mem.add_slang(scope, "低置信", "不该注入", "auto", 0.2)
    check("手写条目存在", (await mem.get_slang(scope, "yyds"))["meaning"] == "永远的神")
    check("自动不能覆盖手写", not await mem.add_slang(scope, "yyds", "自动改的", "auto", 0.9))
    check("手写内容没被改", (await mem.get_slang(scope, "yyds"))["meaning"] == "永远的神")
    hits = await mem.find_slang(scope, "这波 yyds，草")
    check("命中黑话", {h["term"] for h in hits} == {"yyds", "草"}, hits)
    check("低置信不注入", "低置信" not in {h["term"] for h in hits}, hits)
    check("列表有 3 条", len(await mem.list_slang(scope)) == 3)
    check("删除成功", await mem.delete_slang(scope, "草") and not await mem.get_slang(scope, "草"))

    print("[3] 学习状态")
    await mem.set_last_learn(scope, 123.0)
    check("last_learn 读回", await mem.get_last_learn(scope) == 123.0)

    print("[4] 命令")
    orig_admin = bot.store.is_admin
    async def fake_admin(openid):
        return True
    bot.store.is_admin = fake_admin
    try:
        m = FakeMessage("/叫我 小明")
        await bot.handle_command(m, None, scope, "G", "/叫我 小明")
        check("/叫我 回复", "小明" in m.text, m.text)
        check("/叫我 入库", await mem.get_alias(scope, "u1") == "小明")

        m2 = FakeMessage("/黑话 添加 test 测试词")
        await bot.handle_command(m2, None, scope, "G", "/黑话 添加 test 测试词")
        check("/黑话 添加 回复", "test=测试词" in m2.text, m2.text)
        check("/黑话 添加 入库", (await mem.get_slang(scope, "test"))["meaning"] == "测试词")

        m3 = FakeMessage("/黑话 test")
        await bot.handle_command(m3, None, scope, "G", "/黑话 test")
        check("/黑话 查询", "测试词" in m3.text, m3.text)

        m4 = FakeMessage("/印象")
        await bot.handle_command(m4, None, scope, "G", "/印象")
        check("/印象 回复", "小明" in m4.text or "常聊终末地" in m4.text, m4.text)
    finally:
        bot.store.is_admin = orig_admin

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
