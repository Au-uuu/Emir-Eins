# -*- coding: utf-8 -*-
"""随机接话的群友画像 + 主动插话提示测试（不联网、不发群）。"""

import asyncio
import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_profiles_")
os.environ["QQ_BOT_MEMORY"] = "1"
os.environ["QQ_BOT_MEMORY_DB"] = os.path.join(TMP, "memory.db")
os.environ["QQ_BOT_GROUP_CONTEXT"] = "1"
os.environ["QQ_BOT_GROUP_CONTEXT_DB"] = os.path.join(TMP, "gc.db")
os.environ["QQ_BOT_GROUP_CONTEXT_MAX"] = "80"
os.environ["QQ_BOT_GROUP_CONTEXT_MINUTES"] = "180"
os.environ["QQ_BOT_GROUP_CONTEXT_CHARS"] = "5000"
os.environ["QQ_BOT_CHAT_DB"] = os.path.join(TMP, "chat.db")
os.environ["QQ_BOT_CHAT_COOLDOWN"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MIN"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MAX"] = "0"
os.environ["QQ_BOT_CHAT_SPLIT"] = "0"
os.environ["QQ_BOT_WEBSEARCH"] = "0"
os.environ["QQ_BOT_VL_MODEL"] = "off"
os.environ["QQ_BOT_USAGE"] = "0"
os.environ["QQ_BOT_KB_DIR"] = os.path.join(TMP, "kb")

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

    def __init__(self, content="", group="G", openid="u1"):
        FakeMessage._seq += 1
        self.id = f"profile{FakeMessage._seq}"
        self.content = content
        self.group_openid = group
        self.author = types.SimpleNamespace(member_openid=openid, user_openid=openid)
        self.attachments = []
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}


async def main() -> int:
    gid = "GPROFILE"
    scope = f"group:{gid}"

    print("[1] 最近活跃群友")
    rows = [
        ("u1", "小明", "今晚打什么"),
        ("u1", "小明", "还是排位"),
        ("u2", "阿花", "我想看新番"),
        ("u2", "阿花", "有人一起吗"),
        ("u2", "阿花", "没人就算了"),
        ("u3", "路人", "路过"),
    ]
    for i, (oid, name, text) in enumerate(rows):
        await bot.group_ctx.remember(gid, f"pm{i}", oid, text, name)
    members = await bot.group_ctx.recent_members(gid, limit=80)
    check("按发言数排序", [m["openid"] for m in members][:2] == ["u2", "u1"], members)
    check("取到群昵称", members[0]["name"] == "阿花" and members[0]["count"] == 3, members)

    print("[2] 群友画像")
    await bot.memory.set_alias(scope, "u1", "小明")
    await bot.memory.set_impression(scope, "u1", "话多，爱约人打排位", "auto")
    await bot.memory.set_alias(scope, "u2", "阿花")
    await bot.memory.set_impression(scope, "u2", "夜猫子，爱追新番", "auto")
    profiles = await bot.memory.render_profiles(scope, members)
    check("画像带称呼和印象", "小明" in profiles and "排位" in profiles, profiles)
    check("画像带第二个群友", "阿花" in profiles and "新番" in profiles, profiles)
    check("没印象的人不硬编", "路人" not in profiles, profiles)
    check("有统一标题", profiles.startswith("【群友画像】"), profiles)

    print("[3] 随机接话注入上下文 + 画像 + 主动提示")
    seen = {}

    async def fake_generate(history, text, session=""):
        seen["text"] = text
        return "【考察】排位可以，本机负责赢。"

    orig_generate = persona.generate
    orig_available = persona.available
    persona.generate = fake_generate
    persona.available = lambda: True
    try:
        await bot.chat.clear(scope)
        m = FakeMessage("今晚打什么", openid="u1")
        await bot.persona_reply(m, scope, "今晚打什么", proactive=True)
        injected = seen.get("text", "")
        check("注入了群聊上下文", "群聊最近消息" in injected, injected)
        check("注入了群友画像", "【群友画像】" in injected and "小明" in injected, injected)
        check("明确是主动插话", "【触发方式】" in injected and "不是被点名" in injected, injected)
        check("回复已发出", len(m.replies) == 1, m.replies)

        seen.clear()
        m2 = FakeMessage("在吗", openid="u1")
        await bot.chat.clear(scope)
        await bot.persona_reply(m2, scope, "在吗")
        plain = seen.get("text", "")
        check("被动回复不注主动提示", "【触发方式】" not in plain, plain)
        check("被动回复不注群友画像", "【群友画像】" not in plain, plain)
    finally:
        persona.generate = orig_generate
        persona.available = orig_available

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
