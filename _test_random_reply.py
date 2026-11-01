# -*- coding: utf-8 -*-
"""群友随机接话：概率、冷却、过滤、处理器接线。"""

import asyncio
import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["QQ_BOT_GROUP_CONTEXT"] = "0"
os.environ["QQ_BOT_GROUP_CONTEXT_DB"] = os.path.join(tempfile.mkdtemp(), "gc.db")
os.environ["QQ_BOT_MEMORY"] = "0"
os.environ["QQ_BOT_MEMORY_DB"] = os.path.join(tempfile.mkdtemp(), "mem.db")
os.environ["QQ_BOT_CHAT_DB"] = os.path.join(tempfile.mkdtemp(), "chat.db")
os.environ["QQ_BOT_USAGE"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MIN"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MAX"] = "0"
os.environ["QQ_BOT_CHAT_SPLIT"] = "0"
os.environ["QQ_BOT_RANDOM_REPLY"] = "1"       # 测试里固定 100%
os.environ["QQ_BOT_RANDOM_REPLY_COOLDOWN"] = "0"
os.environ["QQ_BOT_TEXT_KEY"] = "test-key"    # 让 persona.available() 为真
os.environ["QQ_BOT_WEBSEARCH"] = "0"
os.environ["QQ_BOT_KB_DIR"] = os.path.join(tempfile.mkdtemp(), "kb")

import bot  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    _seq = 0

    def __init__(self, content="", group="RG"):
        FakeMessage._seq += 1
        self.id = f"rr{FakeMessage._seq}"
        self.content = content
        self.group_openid = group
        self.author = types.SimpleNamespace(member_openid="u", user_openid="u")
        self.attachments = []
        self.mentions = []

    async def reply(self, **kwargs):
        self.replies = kwargs
        return {"id": "sent"}


async def main() -> int:
    print("[1] 概率与过滤")
    check("100% 时普通消息通过", bot.should_random_reply("RG", "随便聊聊") is True)
    check("空消息不接", bot.should_random_reply("RG", "   ") is False)
    check("斜杠指令不接", bot.should_random_reply("RG", "/图库") is False)
    check("纯确认词不接", bot.should_random_reply("RG", "ok") is False)
    check("语气词不接", bot.should_random_reply("RG", "嗷") is False)
    check("资讯卡片不接", bot.should_random_reply("RG", "【新闻资讯】今日") is False)
    orig_quoted = bot.quoted_content
    bot.quoted_content = lambda m: "引用了别人"
    check("引用别人不接", bot.should_random_reply("RG", "你说呢", object()) is False)
    bot.quoted_content = orig_quoted

    orig_p = bot.RANDOM_REPLY_P
    bot.RANDOM_REPLY_P = 0.0
    check("概率 0 时永远不接", bot.should_random_reply("RG", "随便聊聊") is False)
    bot.RANDOM_REPLY_P = orig_p

    orig_raw = bot.get_raw
    bot.get_raw = lambda m: {"author": {"bot": True, "username": "feed"}}
    check("机器人消息识别", bot.author_is_bot(object(), "bot") is True)
    bot.get_raw = lambda m: {"author": {"username": "bot"}}
    check("自己消息识别", bot.author_is_bot(object(), "bot") is True)
    bot.get_raw = lambda m: {"author": {"username": "human"}}
    check("人类消息不误杀", bot.author_is_bot(object(), "bot") is False)
    bot.get_raw = orig_raw
    print("[2] 每群冷却")
    orig_cd = bot.random_reply_cooldown
    bot.random_reply_cooldown = bot.Cooldown(999)
    check("同群第一次通过", bot.should_random_reply("RG2", "第一句") is True)
    check("同群冷却期内拒绝", bot.should_random_reply("RG2", "第二句") is False)
    check("换群不受影响", bot.should_random_reply("RG3", "别的群") is True)
    bot.random_reply_cooldown = orig_cd

    print("[3] 处理器接线（没 @、没关键词时随机接）")
    captured = []

    async def fake_handle_command(*args, **kwargs):
        return False

    async def fake_persona_reply(message, scope, content, proactive=False):
        captured.append((scope, content, proactive))

    orig_handle = bot.handle_command
    orig_persona = bot.persona_reply
    orig_keyword = bot.KEYWORD
    bot.handle_command = fake_handle_command
    bot.persona_reply = fake_persona_reply
    bot.KEYWORD = ""
    try:
        client = object.__new__(bot.MyClient)
        client.api = None
        msg = FakeMessage("这句没人叫我", "RG4")
        await bot.MyClient.on_group_message_create(client, msg)
        check("随机接话会调用 persona_reply", len(captured) == 1 and captured[0][0] == "group:RG4", captured)
        check("随机接话标记为主动", bool(captured) and captured[0][2] is True, captured)

        captured.clear()
        bot.RANDOM_REPLY_P = 0.0
        msg2 = FakeMessage("这句也不接", "RG5")
        await bot.MyClient.on_group_message_create(client, msg2)
        check("概率 0 时处理器不接", captured == [], captured)
    finally:
        bot.handle_command = orig_handle
        bot.persona_reply = orig_persona
        bot.KEYWORD = orig_keyword
        bot.RANDOM_REPLY_P = orig_p

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
