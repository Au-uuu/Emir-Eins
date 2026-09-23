"""
回复策略测试：

  - 群里 @ 机器人后，不再回复「收到：xxx」
  - 认不出的指令（非指令内容）直接无视，不回复
  - 只有 @ 没有任何内容 → 视为 /help
  - 单聊同样：空内容 → /help，非指令内容 → 无视
"""

import asyncio
import sys
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\DSH\qqbot")

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

    print("\n[8] 代码里不再出现「收到：」回显")
    import re

    src = open(os.path.join(r"D:\DSH\qqbot", "bot.py"), encoding="utf-8").read()
    check("无『收到：』字面量", "收到：" not in src)
    check("无『你好，我是机器人』", "你好，我是机器人" not in src)

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
