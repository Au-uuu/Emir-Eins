"""
添加相关测试：

  - 入库提示把「格式」和「大小」并到同一行（省一行）
  - 同一条消息附带图片时，添加必须带斜杠；引用图片时才可省略斜杠
  - /批量添加：一次把消息里的多张图片入库（必须带斜杠）
"""

import asyncio
import os
import shutil
import struct
import sys
import types
import zlib

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\DSH\qqbot")

import bot  # noqa: E402
from image_store import ImageStore  # noqa: E402

TEST_DIR = r"D:\DSH\qqbot\_test_add"
failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


def make_png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class FakeAttachment:
    def __init__(self, url, content_type="image/png"):
        self.url = url
        self.filename = os.path.basename(url)
        self.content_type = content_type
        self.width = None
        self.height = None
        self.size = None


class FakeMessage:
    _seq = 0

    def __init__(self, content="", attachments=None):
        FakeMessage._seq += 1
        self.content = content
        self.attachments = attachments or []
        self.id = f"m{FakeMessage._seq}"
        self.group_openid = "G_TEST"
        self.author = types.SimpleNamespace(member_openid="user_x")
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}

    @property
    def text(self) -> str:
        return "\n".join(r.get("content") or "" for r in self.replies)


async def main() -> int:
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR)

    store = ImageStore(
        db_path=os.path.join(TEST_DIR, "images.db"),
        images_dir=os.path.join(TEST_DIR, "images"),
    )
    bot.store = store

    def write(name: str, color) -> str:
        path = os.path.join(TEST_DIR, name)
        with open(path, "wb") as fh:
            fh.write(make_png(8, 8, color))
        return "file:///" + path.replace("\\", "/")

    url1 = write("a.png", (255, 0, 0))
    url2 = write("b.png", (0, 0, 255))
    url3 = write("c.png", (0, 200, 0))

    print("\n[1] 同消息附图：不带斜杠 -> 忽略")
    m = FakeMessage("添加 单词", attachments=[FakeAttachment(url1)])
    await bot.do_add_image(m, None, "group", "G", "添加 单词")
    check("没有任何回复", m.replies == [], str(m.replies))
    check("没有入库", len(await store.search_images("单词")) == 0)

    print("\n[2] 同消息附图：带斜杠 -> 入库，且格式/大小同一行")
    m2 = FakeMessage("/添加 单词", attachments=[FakeAttachment(url1)])
    await bot.do_add_image(m2, None, "group", "G", "/添加 单词")
    check("入库成功", "入库成功" in m2.text, m2.text)
    lines = m2.text.splitlines()
    check("共 4 行（省了一行）", len(lines) == 4, str(lines))
    check("大小在同一行且格式放括号里",
          any(ln.startswith("大小：") and "（PNG）" in ln for ln in lines), str(lines))
    check("没有独立的『格式：』行", not any(ln.startswith("格式：") for ln in lines))
    check("确实入库了", len(await store.search_images("单词")) == 1)

    print("\n[3] /批量添加：必须带斜杠")
    m3 = FakeMessage("批量添加 批", attachments=[FakeAttachment(url2)])
    await bot.do_batch_add(m3, None, "group", "G", "批量添加 批")
    check("无斜杠批量被忽略", m3.replies == [], str(m3.replies))

    m4 = FakeMessage(
        "/批量添加 批", attachments=[FakeAttachment(url1), FakeAttachment(url2)]
    )
    await bot.do_batch_add(m4, None, "group", "G", "/批量添加 批")
    check("批量完成提示", "批量添加完成" in m4.text, m4.text)
    check("新增 1 张、重复 1 张",
          "新增 1 张" in m4.text and "重复 1 张" in m4.text, m4.text)
    check("批关键词下有 2 张", len(await store.search_images("批")) == 2,
          str(len(await store.search_images("批"))))

    print("\n[4] 批量添加：没有图片时给用法")
    m5 = FakeMessage("/批量添加 批")
    await bot.do_batch_add(m5, None, "group", "G", "/批量添加 批")
    check("提示用法", "用法" in m5.text, m5.text)

    print("\n[5] handle_command 能分发 /批量添加（带斜杠）")
    m6 = FakeMessage("/批量添加 批2", attachments=[FakeAttachment(url3)])
    handled = await bot.handle_command(m6, None, "group", "G", "/批量添加 批2")
    check("返回 True", handled is True)
    check("已入库", len(await store.search_images("批2")) == 1)

    m7 = FakeMessage("批量添加 批3", attachments=[FakeAttachment(url3)])
    handled7 = await bot.handle_command(m7, None, "group", "G", "批量添加 批3")
    check("无斜杠不分发（透传为 False）", handled7 is False, str(handled7))

    print("\n[6] 开头提及（<@openid> 与 @昵称）过滤")
    check("剥 <@openid> 前缀",
          bot.normalize_incoming("<@ABC123> /图库") == "/图库",
          bot.normalize_incoming("<@ABC123> /图库"))
    check("剥多个提及",
          bot.normalize_incoming("<@A> <@B> /来只 猫") == "/来只 猫",
          bot.normalize_incoming("<@A> <@B> /来只 猫"))
    check("剥 @昵称 前缀",
          bot.normalize_incoming("@张三 添加 猫") == "添加 猫",
          bot.normalize_incoming("@张三 添加 猫"))
    check("split_keywords 丢弃提及 token",
          bot.split_keywords("猫 <@A> 狗 @某人 鸟") == ["猫", "狗", "鸟"],
          str(bot.split_keywords("猫 <@A> 狗 @某人 鸟")))

    face = '<faceType=6, faceId="0", ext="eyJ0ZXh0IjoiIn0=">'
    check("剥掉表情标记",
          bot.normalize_incoming(f"早上好{face}") == "早上好",
          repr(bot.normalize_incoming(f"早上好{face}")))
    check("表情标记不进关键词",
          bot.split_keywords(f"早上好{face} 猫") == ["早上好", "猫"],
          str(bot.split_keywords(f"早上好{face} 猫")))

    u9 = write("n9.png", (9, 9, 9))
    raw = "<@SOMEONE> /添加 提及词"
    content = bot.normalize_incoming(raw)  # 走真实流程：先归一化再分派
    m = FakeMessage(content, attachments=[FakeAttachment(u9)])
    await bot.handle_command(m, None, "group", "G", content)
    check("带提及的 /添加 能入库", "入库成功" in m.text, m.text)
    check("提及词已入库", len(await store.search_images("提及词")) == 1)

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
