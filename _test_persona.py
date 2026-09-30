"""人格聊天测试：敏感词过滤、会话记忆隔离、回复流程的静默策略。

不需要真的调模型 —— 用假的 generate() 替换掉 persona.generate。
"""
import asyncio
import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot  # noqa: E402
import persona  # noqa: E402
import sensitive  # noqa: E402
from chat_store import ChatStore  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    _seq = 0

    def __init__(self, content: str = ""):
        FakeMessage._seq += 1
        self.content = content
        self.id = f"p{FakeMessage._seq}"
        self.group_openid = "G_TEST"
        self.replies: list[dict] = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}

    @property
    def text(self) -> str:
        return "\n".join(r.get("content") or "" for r in self.replies)


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="qqbot_persona_")

    # ---------------- 1. 敏感词过滤 ----------------
    print("\n[1] 敏感词过滤")
    words_path = os.path.join(tmp, "words.txt")
    with open(words_path, "w", encoding="utf-8") as fh:
        fh.write("# 注释行\n测试敏感词\n另一个词\n单\n")

    filt = sensitive.WordFilter(words_path)
    check("词表加载（单字词被丢弃）", filt.size == 2, f"size={filt.size}")
    check("直接命中", filt.hit("这里出现测试敏感词了") == "测试敏感词")
    check("未命中返回 None", filt.hit("今天天气不错") is None)
    check("空格绕过拦得住", filt.hit("测 试 敏 感 词") == "测试敏感词")
    check("符号绕过拦得住", filt.hit("测*试·敏-感_词") == "测试敏感词")
    check("全角绕过拦得住", filt.hit("测　　试－敏感词") == "测试敏感词")
    check("单字词不会误杀", filt.hit("单选一个") is None)

    missing = sensitive.WordFilter(os.path.join(tmp, "nope.txt"))
    check("词表缺失时自动禁用", missing.enabled is False)
    check("禁用时空转", missing.hit("测试敏感词") is None)

    # ---------------- 2. 会话记忆 ----------------
    print("\n[2] 会话记忆：按会话隔离 + 上限裁剪")
    store = ChatStore(db_path=os.path.join(tmp, "chat.db"), max_messages=4)
    await store.append("group:A", "你好", "【肯定】你好")
    await store.append("group:B", "另一个群的消息", "【回答】在")
    hb = await store.history("group:B")
    check("群与群不串台", len(hb) == 2 and hb[0]["content"] == "另一个群的消息", str(hb))

    for i in range(10):
        await store.append("group:A", f"m{i}", f"r{i}")
    ha = await store.history("group:A")
    check("超出上限被裁剪", len(ha) == 4, f"len={len(ha)}")
    check("保留的是最近的", ha[-1]["content"] == "r9", ha[-1]["content"])

    await store.append("group:C", "只有用户这句", None)
    hc = await store.history("group:C")
    check("reply 为空时只记用户那句", len(hc) == 1 and hc[0]["role"] == "user", str(hc))

    check("默认上限 100 条", ChatStore(db_path=os.path.join(tmp, "d.db")).max_messages == 100)

    # ---------------- 2b. 记忆过期（20 分钟） ----------------
    print("\n[2b] 记忆过期：超过存活时间的旧消息不再喂给模型")
    import sqlite3
    import time as _time

    ttl_path = os.path.join(tmp, "ttl.db")
    ttl_store = ChatStore(db_path=ttl_path, max_messages=100, memory_minutes=20)
    await ttl_store.append("c2c:u1", "刚说的话", "【回答】好")

    conn = sqlite3.connect(ttl_path)
    conn.execute(
        "INSERT INTO messages(scope, role, content, ts) VALUES (?, ?, ?, ?)",
        ("c2c:u1", "user", "21分钟前的旧话", _time.time() - 21 * 60),
    )
    conn.commit()
    conn.close()

    texts = [x["content"] for x in await ttl_store.history("c2c:u1")]
    check(
        "过期消息被排除在上下文外",
        "21分钟前的旧话" not in texts and "刚说的话" in texts,
        str(texts),
    )

    await ttl_store.append("c2c:u1", "触发一次写入", "【回答】好")
    conn = sqlite3.connect(ttl_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE scope='c2c:u1' AND content='21分钟前的旧话'"
    ).fetchone()[0]
    conn.close()
    check("写入时物理清理过期行", n == 0, f"残留 {n} 行")

    # ---------------- 3. 回复流程 ----------------
    print("\n[3] 回复流程的静默策略")
    bot.chat = ChatStore(db_path=os.path.join(tmp, "chat2.db"), max_messages=100)
    bot.sensitive.filter = filt
    bot.chat_cooldown.seconds = 0
    persona.available = lambda: True

    async def ok_generate(history, text):
        return f"【回答】收到「{text}」上下文{len(history)}条"

    async def none_generate(history, text):
        return None

    async def bad_generate(history, text):
        return "这条回复里有测试敏感词"

    persona.generate = ok_generate
    m = FakeMessage("今天天气不错")
    await bot.persona_reply(m, "group:G1", "今天天气不错")
    check("正常回复", "收到" in m.text, m.text)
    check("上下文已写入", len(await bot.chat.history("group:G1")) == 2)

    m = FakeMessage("这是测试敏感词")
    await bot.persona_reply(m, "group:G1", "这是测试敏感词")
    check("用户命中敏感词 → 静默", m.replies == [], str(m.replies))
    check("且不写进上下文", len(await bot.chat.history("group:G1")) == 2)

    persona.generate = none_generate
    m = FakeMessage("模型会失败")
    await bot.persona_reply(m, "group:G1", "模型会失败")
    check("模型返回空 → 静默", m.replies == [], str(m.replies))

    persona.generate = bad_generate
    m = FakeMessage("正常提问")
    await bot.persona_reply(m, "group:G2", "正常提问")
    check("模型回复命中敏感词 → 丢弃不发送", m.replies == [], str(m.replies))
    h = await bot.chat.history("group:G2")
    check("只记用户那句（回复不回滚）", len(h) == 1 and h[0]["role"] == "user", str(h))

    persona.generate = ok_generate
    bot.chat_cooldown.seconds = 60
    bot.chat_cooldown._last.clear()
    m1 = FakeMessage("第一次")
    m2 = FakeMessage("第二次")
    await bot.persona_reply(m1, "group:G3", "第一次")
    await bot.persona_reply(m2, "group:G3", "第二次")
    check(
        "限流内只回一次",
        len(m1.replies) == 1 and m2.replies == [],
        f"{len(m1.replies)}/{len(m2.replies)}",
    )
    m3 = FakeMessage("别的会话")
    await bot.persona_reply(m3, "group:G4", "别的会话")
    check("限流按会话独立", len(m3.replies) == 1, str(m3.replies))

    persona.available = lambda: False
    m = FakeMessage("没配 Key")
    await bot.persona_reply(m, "group:G5", "没配 Key")
    check("未配置 API Key → 完全不回复", m.replies == [], str(m.replies))

    # ---------------- 4. 全量模式：靠 mentions 判定 @ ----------------
    print("\n[4] 全量模式：靠 payload 的 mentions[].is_you 判定「被 @」")
    import raw_events

    class GroupFakeMessage:
        _seq = 0

        def __init__(self, content: str, mentions=None):
            GroupFakeMessage._seq += 1
            self.content = content
            self.attachments: list = []
            self.id = f"g{GroupFakeMessage._seq}"
            self.group_openid = "G_ALL"
            self.author = types.SimpleNamespace(member_openid="user_x")
            self.replies: list[dict] = []
            if mentions is not None:
                raw_events._remember(self.id, {"d": {"mentions": mentions}})

        async def reply(self, **kwargs):
            self.replies.append(kwargs)
            return {"id": "sent"}

        @property
        def text(self) -> str:
            return "\n".join(r.get("content") or "" for r in self.replies)

    class GroupFakeApi:
        async def post_group_message(self, **kwargs):
            return {"id": "m"}

    group_client = types.SimpleNamespace(api=GroupFakeApi())

    async def group_all(m):
        await bot.MyClient.on_group_message_create(group_client, m)

    persona.available = lambda: True
    persona.generate = ok_generate
    bot.chat_cooldown.seconds = 0

    m = GroupFakeMessage(
        "<@BOTOPENID> 111",
        mentions=[{"is_you": True, "bot": True, "username": "依蜜尔爱因-测试中"}],
    )
    await group_all(m)
    check("明确 @ 机器人 → 人格回复", len(m.replies) == 1, str(m.replies))

    m2 = GroupFakeMessage(
        "<@SOMEONE> 你好啊",
        mentions=[{"is_you": False, "bot": False, "username": "别人"}],
    )
    await group_all(m2)
    check("回复别人（mentions 无 is_you）→ 不回复", m2.replies == [], str(m2.replies))

    m3 = GroupFakeMessage("222")
    await group_all(m3)
    check("普通群消息（无 mentions）→ 不回复", m3.replies == [], str(m3.replies))

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
