# -*- coding: utf-8 -*-
"""群聊上下文缓冲测试：存取、去重、隔离、过期、注入人格链路。"""

import asyncio
import os
import sqlite3
import sys
import tempfile
import time
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_groupctx_")
os.environ["QQ_BOT_GROUP_CONTEXT"] = "1"
os.environ["QQ_BOT_GROUP_CONTEXT_DB"] = os.path.join(TMP, "gc.db")
os.environ["QQ_BOT_CHAT_DELAY_MIN"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MAX"] = "0"
os.environ["QQ_BOT_CHAT_SPLIT"] = "0"
os.environ["QQ_BOT_GROUP_CONTEXT_MAX"] = "5"
os.environ["QQ_BOT_GROUP_CONTEXT_MINUTES"] = "30"
os.environ["QQ_BOT_GROUP_CONTEXT_CHARS"] = "800"
os.environ["QQ_BOT_WEBSEARCH"] = "0"
os.environ["QQ_BOT_KB_DIR"] = os.path.join(TMP, "kb")

import bot  # noqa: E402
import group_context  # noqa: E402
import persona  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    _seq = 0

    def __init__(self, content="", group_openid="G_TEST", openid="user_x"):
        FakeMessage._seq += 1
        self.id = f"m{FakeMessage._seq}"
        self.content = content
        self.group_openid = group_openid
        self.author = types.SimpleNamespace(
            member_openid=openid, user_openid=openid
        )
        self.attachments = []
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}


async def main() -> int:
    store = bot.group_ctx

    print("[1] 存取与顺序")
    for i in range(1, 8):
        await store.remember("G1", f"m{i}", f"openid{i:06d}", f"第{i}条")
    text = await store.render("G1", exclude_id="m7")
    check("只取最近 5 条（去掉当前这条后剩 4 条）", "第3条" in text and "第6条" in text, text)
    check("更早的被裁掉", "第1条" not in text and "第2条" not in text, text)
    check("当前消息被排除", "第7条" not in text, text)
    check("顺序是旧→新", text.index("第3条") < text.index("第6条"), text)
    check("带发言人尾号", "用户000003" in text, text)
    check("带头部/尾部说明", "群聊最近消息" in text and "下面是需要你回应" in text)

    await store.remember("G1", "m8", "openid000008", "带昵称", "小明")
    text2 = await store.render("G1", exclude_id="m9")
    check("优先显示群昵称/QQ昵称", "小明: 带昵称" in text2, text2)

    print("[2] 去重与群隔离")
    await store.remember("G1", "m6", "openid000006", "第6条（重复投递）")
    check("同一 msg_id 不重复（且只留 5 条）", (await store.count("G1")) == 5, await store.count("G1"))
    check("别的群看不到", (await store.render("G2")) == "")
    await store.remember("G2", "x1", "openidX", "另一个群")
    check("别的群只有自己那条", "另一个群" in await store.render("G2"))

    print("[3] 命令与空内容")
    await store.remember("G3", "c1", "openidC", "/图库")
    await store.remember("G3", "c2", "openidC", "   ")
    check("斜杠命令不入上下文", (await store.render("G3")) == "")

    print("[4] 过期与手动清理")
    old = group_context.GroupContextStore(
        db_path=os.path.join(TMP, "old.db"), max_messages=5, minutes=1, max_chars=500
    )
    await old.remember("G4", "old1", "openidO", "很久以前")
    with sqlite3.connect(os.path.join(TMP, "old.db")) as conn:
        conn.execute("UPDATE group_messages SET ts = ? WHERE msg_id = 'old1'", (time.time() - 3600,))
    check("过期消息不返回", (await old.render("G4")) == "")
    await old.remember("G4", "new1", "openidN", "刚刚")
    check("新消息仍在", "刚刚" in await old.render("G4"))
    check("clear 清空", (await old.clear("G4")) == 1 and (await old.count("G4")) == 0)

    print("[4b] 旧库迁移补 display_name 列")
    old_path = os.path.join(TMP, "legacy.db")
    with sqlite3.connect(old_path) as conn:
        conn.execute(
            "CREATE TABLE group_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, group_openid TEXT NOT NULL, "
            "msg_id TEXT NOT NULL, openid TEXT NOT NULL DEFAULT '', "
            "content TEXT NOT NULL, ts REAL NOT NULL, "
            "UNIQUE(group_openid, msg_id))"
        )
    legacy = group_context.GroupContextStore(db_path=old_path, max_messages=5, minutes=30, max_chars=500)
    await legacy.remember("G5", "l1", "openidL", "旧库消息", "旧昵称")
    check("旧库能写入并能读到昵称", "旧昵称: 旧库消息" in (await legacy.render("G5")), await legacy.render("G5"))

    print("[5] 注入人格链路（只群聊，不重复当前消息）")
    seen = {}

    async def fake_generate(history, text, session=""):
        seen["text"] = text
        return "好"

    orig_generate = persona.generate
    orig_available = persona.available
    persona.generate = fake_generate
    persona.available = lambda: True
    try:
        await store.remember("GC1", "p1", "openidP1", "刚才在聊终末地")
        await store.remember("GC1", "p2", "openidP2", "还有新版本")
        m = FakeMessage("现在呢", group_openid="GC1", openid="openidP3")
        await bot.chat.clear("group:GC1")
        await bot.persona_reply(m, "group:GC1", "现在呢")
        injected = seen.get("text", "")
        check("群聊背景进了输入", "刚才在聊终末地" in injected, injected)
        check("当前消息只出现一次", injected.count("现在呢") == 1, injected)
        check("带群聊头部", "群聊最近消息" in injected, injected)

        seen.clear()
        m2 = FakeMessage("单聊内容", openid="openidU1")
        await bot.chat.clear("c2c:U1")
        await bot.persona_reply(m2, "c2c:U1", "单聊内容")
        check("单聊不注入群聊背景", "群聊最近消息" not in seen.get("text", ""), seen.get("text"))
    finally:
        persona.generate = orig_generate
        persona.available = orig_available

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
