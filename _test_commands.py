"""
指令函数级测试：用假 message 对象直接调用 do_random_image / do_add_image / do_gallery。

这类测试能在不依赖真实群消息的情况下，抓出参数不匹配、分支遗漏等错误
（send_image() 缺参数那个 bug 就是这样漏掉的）。
"""

import asyncio
import os
import shutil
import sys
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\DSH\qqbot")

import bot  # noqa: E402
from image_store import ImageStore  # noqa: E402

TEST_DIR = r"D:\DSH\qqbot\_test_cmd"
failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    """最小可用的假消息对象，记录所有 reply 调用。"""

    def __init__(self, content="", attachments=None, msg_id="m1"):
        self.content = content
        self.attachments = attachments or []
        self.id = msg_id
        self.group_openid = "G_TEST"
        self.author = types.SimpleNamespace(member_openid="user_x")
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}

    @property
    def text_replies(self):
        return [r.get("content") for r in self.replies if r.get("content")]

    @property
    def sent_images(self):
        return [r for r in self.replies if r.get("msg_type") == 7]


class FakeApi:
    """假 API，记录媒体发送，不真的联网。"""

    def __init__(self):
        self.media_sent = []
        self.uploaded = []

    async def post_group_message(self, **kwargs):
        self.media_sent.append(kwargs)
        return {"id": "m"}


class FakeUploader:
    """替换真实上传器，避免联网。"""

    def __init__(self, *a, **k):
        pass

    async def upload_group_image(self, group_openid, path, name=None):
        return "FAKE_FILE_INFO"

    async def upload_c2c_image(self, openid, path, name=None):
        return "FAKE_FILE_INFO"


async def main() -> int:
    # 用独立的临时库
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR)
    bot.store = ImageStore(
        db_path=os.path.join(TEST_DIR, "images.db"),
        images_dir=os.path.join(TEST_DIR, "images"),
    )
    bot.MediaUploader = FakeUploader

    from PIL import Image
    import io

    def png_bytes(color=(200, 50, 50)) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (40, 40), color).save(buf, format="PNG")
        return buf.getvalue()

    def write(name, data):
        p = os.path.join(TEST_DIR, name)
        with open(p, "wb") as fh:
            fh.write(data)
        return "file:///" + p.replace("\\", "/")

    url1 = write("a.png", png_bytes((200, 50, 50)))
    url2 = write("b.png", png_bytes((50, 50, 200)))

    print("[1] 入库两张图")
    r1 = await bot.store.add_from_attachment(url1, ["大肥鱼", "deepseek"])
    r2 = await bot.store.add_from_attachment(url2, ["deepseek"])
    check("两张图入库", r1.image_id and r2.image_id, f"{r1.image_id},{r2.image_id}")

    print("\n[2] /来只 无关键词（之前崩在这里）")
    m = FakeMessage("/来只")
    try:
        await bot.do_random_image(m, FakeApi(), "group", "G_TEST", "/来只")
        check("未抛异常", True)
        check("发出了图片消息", len(m.sent_images) == 1,
              f"replies={m.replies}")
        check("图片消息带 file_info",
              m.sent_images and m.sent_images[0].get("media"))
    except Exception as exc:  # noqa: BLE001
        check("未抛异常", False, f"{type(exc).__name__}: {exc}")

    print("\n[3] /来只 关键词 deepseek")
    m = FakeMessage("/来只 deepseek")
    try:
        await bot.do_random_image(m, FakeApi(), "group", "G_TEST", "/来只 deepseek")
        check("发出了图片消息", len(m.sent_images) == 1)
    except Exception as exc:  # noqa: BLE001
        check("未抛异常", False, f"{type(exc).__name__}: {exc}")

    print("\n[4] /来只 不存在的关键词（应给文字提示，不发图）")
    m = FakeMessage("/来只 不存在")
    await bot.do_random_image(m, FakeApi(), "group", "G_TEST", "/来只 不存在")
    check("有文字提示", len(m.text_replies) == 1, str(m.text_replies))
    check("没有发图", len(m.sent_images) == 0)

    print("\n[5] /来只 空图库")
    empty = ImageStore(
        db_path=os.path.join(TEST_DIR, "empty.db"),
        images_dir=os.path.join(TEST_DIR, "empty"),
    )
    real_store = bot.store
    bot.store = empty
    m = FakeMessage("/来只")
    await bot.do_random_image(m, FakeApi(), "group", "G_TEST", "/来只")
    check("空库给提示", len(m.text_replies) == 1, str(m.text_replies))
    bot.store = real_store

    print("\n[6] /添加 无图片（应保持静默，避免全量群里刷屏）")
    m = FakeMessage("/添加 测试")
    await bot.do_add_image(m, FakeApi(), "group", "G_TEST", "/添加 测试")
    check("保持静默", len(m.text_replies) == 0, str(m.text_replies))

    print("\n[7] /图库 各分支")
    m = FakeMessage("/图库")
    await bot.do_gallery(m, "/图库")
    check("默认输出图库列表", "图库列表" in (m.text_replies[0] or ""), str(m.text_replies)[:60])

    m = FakeMessage("/图库 统计")
    await bot.do_gallery(m, "/图库 统计")
    check("统计有输出", "图片总数" in (m.text_replies[0] or ""), str(m.text_replies)[:60])

    m = FakeMessage("/图库 deepseek")
    await bot.do_gallery(m, "/图库 deepseek")
    check("按关键词搜索有输出", "deepseek" in (m.text_replies[0] or ""))

    m = FakeMessage("/图库 不存在")
    await bot.do_gallery(m, "/图库 不存在")
    check("不存在的关键词给提示", "没有图片" in (m.text_replies[0] or ""))

    print("\n[8] /help 分支（带按钮，失败要能兜底）")
    m = FakeMessage("/help")
    await bot.send_help(m)
    check("帮助已发送", len(m.replies) == 1)
    check("带上了键盘", m.replies[0].get("keyboard") is not None)
    check("帮助里写了 /图库 统计", "/图库 统计" in bot.HELP_TEXT)
    check("帮助里写了只有添加/删除/来只可省斜杠",
          "只有「添加 / 删除 / 来只」" in bot.HELP_TEXT)

    print("\n[9] 斜杠强制：图库/关联/私有/公开 必须带 /")
    for bare in ("图库", "关联 a b", "取消关联 a", "查找关联 a", "私有 x", "公开 x"):
        bm = FakeMessage(bare)
        handled = await bot.handle_command(bm, FakeApi(), "group", "G_TEST", bare)
        check(f"裸「{bare}」被无视", handled is False and bm.replies == [],
              f"handled={handled} replies={bm.replies}")
    ok = FakeMessage("/图库")
    check("带「/」的 /图库 正常", await bot.handle_command(
        ok, FakeApi(), "group", "G_TEST", "/图库") is True)

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
