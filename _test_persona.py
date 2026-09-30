"""人格聊天测试：敏感词过滤、会话记忆隔离、回复流程的静默策略。

不需要真的调模型 —— 用假的 generate() 替换掉 persona.generate。
"""
import asyncio
import os
import sys
import tempfile

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

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
