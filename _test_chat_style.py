# -*- coding: utf-8 -*-
"""活人感：长回复拆条 + msg_seq 递增测试。"""

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
os.environ["QQ_BOT_CHAT_SPLIT"] = "1"
os.environ["QQ_BOT_CHAT_SPLIT_CHARS"] = "40"
os.environ["QQ_BOT_CHAT_SPLIT_MAX"] = "2"
os.environ["QQ_BOT_WEBSEARCH"] = "0"
os.environ["QQ_BOT_KB_DIR"] = os.path.join(tempfile.mkdtemp(), "kb")

import bot  # noqa: E402
import persona  # noqa: E402

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
        self.id = f"cs{FakeMessage._seq}"
        self.content = content
        self.group_openid = "G"
        self.author = types.SimpleNamespace(member_openid="u", user_openid="u")
        self.attachments = []
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}


async def main() -> int:
    print("[1] split_reply 边界")
    check("短回复不拆", bot.split_reply("好") == ["好"])
    check("URL 不拆", bot.split_reply("看这个 https://example.com/" + "x" * 80) == [
        "看这个 https://example.com/" + "x" * 80
    ])
    code = "```\n" + "x" * 100 + "\n```"
    check("代码块不拆", bot.split_reply(code) == [code])

    long_text = (
        "今天这个更新其实还行，新角色手感比想象中好一点。"
        "不过抽卡还是老样子，四十发下去什么都没有。"
        "你要是也想抽，建议先看看别人实测再决定。"
    )
    parts = bot.split_reply(long_text, max_parts=2, target=30)
    check("长回复拆成 2 段", len(parts) == 2, parts)
    check("不丢字", "".join(parts) == long_text, parts)
    check("第一段在标点后断开", parts[0][-1] in "。！？!?…", parts[0])
    check("每段都不为空", all(p.strip() for p in parts), parts)

    many = bot.split_reply("第一句。第二句。第三句。第四句。第五句。", max_parts=3, target=10)
    check("不超过 max_parts", len(many) <= 3, many)

    print("[2] persona_reply 拆条发送")
    long_reply = "先说结论，这个能用。不过有两个小坑，第一个是延迟，第二个是配额。你要是急用，我可以先帮你把简单的部分搭起来。"

    async def fake_generate(history, text, session="", temperature=0.8, **kwargs):
        return long_reply

    orig_generate = persona.generate
    orig_available = persona.available
    persona.generate = fake_generate
    persona.available = lambda: True
    try:
        m = FakeMessage("在吗")
        await bot.chat.clear("group:CS1")
        await bot.persona_reply(m, "group:CS1", "在吗")
        check("拆成 2 条发送", len(m.replies) == 2, m.replies)
        check("两条内容拼起来等于原回复", "".join(r["content"] for r in m.replies) == long_reply, m.replies)
        check("msg_seq 从 1 递增", [r.get("msg_seq") for r in m.replies] == [1, 2], m.replies)
    finally:
        persona.generate = orig_generate
        persona.available = orig_available

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
