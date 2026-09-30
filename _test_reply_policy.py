"""
回复策略测试：

  - 群里 @ 机器人后，不再回复「收到：xxx」
  - 认不出的指令（非指令内容）直接无视，不回复
  - 只有 @ 没有任何内容 → 视为 /help
  - 单聊同样：空内容 → /help，非指令内容 → 无视
"""

import asyncio
import os
import sys
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot  # noqa: E402
from image_store import ImageStore  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    _seq = 0

    def __init__(self, content="", scope="group"):
        FakeMessage._seq += 1
        self.content = content
        self.attachments = []
        self.id = f"m{FakeMessage._seq}"
        self.group_openid = "G_TEST"
        if scope == "c2c":
            self.author = types.SimpleNamespace(
                user_openid="user_x", member_openid="user_x"
            )
        else:
            self.author = types.SimpleNamespace(member_openid="user_x")
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}

    @property
    def text(self) -> str:
        return "\n".join(r.get("content") or "" for r in self.replies)


class FakeApi:
    async def post_group_message(self, **kwargs):
        return {"id": "m"}


class FakeClient:
    def __init__(self):
        self.api = FakeApi()


async def main() -> int:
    import os
    import tempfile

    # handle_command 里 /ping 等不碰 store；但导入时已建库，这里换成临时库避免污染
    tmp = tempfile.mkdtemp(prefix="qqbot_reply_")
    bot.store = ImageStore(
        db_path=os.path.join(tmp, "images.db"),
        images_dir=os.path.join(tmp, "images"),
    )
    # 单聊发图会写进人格聊天的上下文，这里换成临时库，别污染 data/chat.db
    from chat_store import ChatStore

    bot.chat = ChatStore(db_path=os.path.join(tmp, "chat.db"))
    # 本套件只测「回复策略」，把人格聊天关掉，避免依赖模型可用性 / 产生真实调用
    bot.persona.available = lambda: False

    client = FakeClient()

    async def group_at(m):
        await bot.MyClient.on_group_at_message_create(client, m)

    async def c2c(m):
        await bot.MyClient.on_c2c_message_create(client, m)

    print("\n[1] 群里只有 @、没有正文 → 视为 /help")
    m = FakeMessage("", scope="group")
    await group_at(m)
    check("有回复", len(m.replies) == 1, str(m.replies))
    check("内容是帮助", "我是群助手" in m.text, m.text[:40])

    print("\n[2] 群里 @ 了但说的是普通内容 → 无视，不回复")
    m = FakeMessage("今天天气不错", scope="group")
    await group_at(m)
    check("没有任何回复", m.replies == [], str(m.replies))

    print("\n[3] 群里 @ 了但指令写错 → 无视，不回复")
    m = FakeMessage("/来芝 猫", scope="group")  # 错别字，不是有效指令
    await group_at(m)
    check("没有任何回复", m.replies == [], str(m.replies))

    print("\n[4] 群里 @ 有效指令 → 正常回复")
    m = FakeMessage("/ping", scope="group")
    await group_at(m)
    check("回复 pong", m.text == "pong 🏓", m.text)

    m2 = FakeMessage("/help", scope="group")
    await group_at(m2)
    check("帮助可正常触发", "我是群助手" in m2.text, m2.text[:20])

    print("\n[5] 单聊空内容 → /help")
    m = FakeMessage("", scope="c2c")
    await c2c(m)
    check("单聊空内容给帮助", "我是群助手" in m.text, m.text[:40])

    print("\n[6] 单聊非指令内容 → 无视")
    m = FakeMessage("在吗", scope="c2c")
    await c2c(m)
    check("单聊不再回收到", m.replies == [], str(m.replies))

    print("\n[7] 单聊有效指令 → 正常回复")
    m = FakeMessage("/ping", scope="c2c")
    await c2c(m)
    check("单聊 ping 正常", m.text == "pong 🏓", m.text)

    print("\n[8] 单聊只发一张图片 → 静默，不回帮助")
    m = FakeMessage("", scope="c2c")
    m.attachments = [
        types.SimpleNamespace(
            content_type="image/png",
            filename="a.png",
            url="http://example.com/a.png",
        )
    ]
    await c2c(m)
    check("单聊发图不回复", m.replies == [], str(m.replies))
    ctx = await bot.chat.history("c2c:user_x")
    check("但会记入上下文", len(ctx) == 1 and "图片" in ctx[0]["content"], str(ctx))

    print("\n[9] 单聊只引用一张图片（图在 msg_elements 里）→ 同样静默")
    import raw_events

    m = FakeMessage("", scope="c2c")
    raw_events._remember(
        m.id,
        {
            "d": {
                "msg_elements": [
                    {
                        "attachments": [
                            {
                                "content_type": "image/jpeg",
                                "filename": "b.jpg",
                                "url": "http://example.com/b.jpg",
                            }
                        ]
                    }
                ]
            }
        },
    )
    await c2c(m)
    check("引用图片也不回复", m.replies == [], str(m.replies))
    ctx = await bot.chat.history("c2c:user_x")
    check("引用图片也记入上下文", len(ctx) == 2, str(ctx))

    print("\n[10] 单聊只发了个表情（空内容、无图）→ 仍给帮助")
    m = FakeMessage("", scope="c2c")
    await c2c(m)
    check("空内容仍给帮助", "我是群助手" in m.text, m.text[:40])

    print("\n[11] 代码里不再出现「收到：」回显")
    import re

    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py"), encoding="utf-8").read()
    check("无『收到：』字面量", "收到：" not in src)
    check("无『你好，我是机器人』", "你好，我是机器人" not in src)

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
