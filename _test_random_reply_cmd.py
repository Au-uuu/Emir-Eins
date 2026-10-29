# -*- coding: utf-8 -*-
"""群聊随机接话 /接话 指令测试（任何人可调、按群生效、可持久化）。"""

import asyncio
import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_rr_cmd_")
os.environ["QQ_BOT_MEMORY"] = "1"
os.environ["QQ_BOT_MEMORY_DB"] = os.path.join(TMP, "memory.db")
os.environ["QQ_BOT_GROUP_CONTEXT"] = "0"
os.environ["QQ_BOT_GROUP_CONTEXT_DB"] = os.path.join(TMP, "gc.db")
os.environ["QQ_BOT_CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["QQ_BOT_CHAT_COOLDOWN"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MIN"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MAX"] = "0"
os.environ["QQ_BOT_CHAT_SPLIT"] = "0"
os.environ["QQ_BOT_RANDOM_REPLY"] = "0.03"
os.environ["QQ_BOT_RANDOM_REPLY_COOLDOWN"] = "0"
os.environ["QQ_BOT_WEBSEARCH"] = "0"
os.environ["QQ_BOT_VL_MODEL"] = "off"
os.environ["QQ_BOT_KB_DIR"] = os.path.join(TMP, "kb")

import bot  # noqa: E402
import random_reply  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    _seq = 0

    def __init__(self, content="", group="G", openid="u1"):
        FakeMessage._seq += 1
        self.id = f"rr{FakeMessage._seq}"
        self.content = content
        self.group_openid = group
        self.author = types.SimpleNamespace(member_openid=openid, user_openid=openid)
        self.attachments = []
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}

    @property
    def text(self):
        return "\n".join(r.get("content") or "" for r in self.replies)


async def send(text, scope=None, group="G"):
    scope = scope or f"group:{group}"
    m = FakeMessage(text, group)
    ok = await bot.handle_command(m, None, scope, group, text)
    return ok, m.text


async def main() -> int:
    await random_reply.load(bot.memory)

    print("[1] 查看默认概率")
    ok, text = await send("/接话")
    check("指令被消费", ok is True, ok)
    check("显示 3%", "3%" in text, text)
    check("说明跟随全局", "全局" in text or "默认" in text, text)

    print("[2] 任何人都能改成本群 5%")
    ok, text = await send("/接话 5%")
    check("设置成功", ok is True and "5%" in text, text)
    check("本群生效 5%", abs(random_reply.probability("G", 0.03) - 0.05) < 1e-9)
    check("其他群仍是 3%", abs(random_reply.probability("OTHER", 0.03) - 0.03) < 1e-9)

    print("[3] 每群独立")
    _, text2 = await send("/接话 10%", group="G2")
    check("G2 设成 10%", "10%" in text2, text2)
    check("G 仍是 5%", abs(random_reply.probability("G", 0.03) - 0.05) < 1e-9)
    check("G2 是 10%", abs(random_reply.probability("G2", 0.03) - 0.10) < 1e-9)

    print("[4] 关闭 / 恢复默认")
    _, text = await send("/接话 关", group="G2")
    check("关闭回复", "关闭" in text, text)
    check("G2 概率 0", random_reply.probability("G2", 0.03) == 0.0)
    _, text = await send("/接话 默认", group="G2")
    check("恢复默认回复", "3%" in text, text)
    check("G2 回到 3%", abs(random_reply.probability("G2", 0.03) - 0.03) < 1e-9)

    print("[5] 上限与非法输入")
    _, text = await send("/接话 100%")
    check("100% 被压到 50%", abs(random_reply.probability("G", 0.03) - 0.5) < 1e-9, text)
    check("提示上限", "上限" in text or "50%" in text, text)
    _, text = await send("/接话 乱写")
    check("非法输入给写法", "写法" in text or "/接话 5%" in text, text)
    check("非法输入不改值", abs(random_reply.probability("G", 0.03) - 0.5) < 1e-9)

    print("[6] 概率真的影响 should_random_reply")
    orig_available = bot.persona.available
    orig_random = bot.random.random
    bot.persona.available = lambda: True
    bot.random.random = lambda: 0.0
    try:
        await send("/接话 5%")
        check("5% 时随机值 0 通过", bot.should_random_reply("G", "随便聊聊") is True)
        await send("/接话 关")
        check("关掉后不接", bot.should_random_reply("G", "随便聊聊") is False)
        await send("/接话 默认")
    finally:
        bot.persona.available = orig_available
        bot.random.random = orig_random

    print("[7] 持久化：重载后仍生效")
    await send("/接话 7%")
    random_reply._overrides.clear()
    await random_reply.load(bot.memory)
    check("重载后 G 是 7%", abs(random_reply.probability("G", 0.03) - 0.07) < 1e-9)

    print("[8] 私聊不处理")
    ok, text = await send("/接话 5%", scope="c2c:U1")
    check("私聊有提示", ok is True and "群里" in text, text)

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
