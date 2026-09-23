"""
预览缩略图 + /图库 <关键词> 发图测试。

  - 入库时生成 ≤100KB 的 JPEG 缩略图
  - /图库 <关键词> 发预览图，每页 8 张，页码可指定
  - 无主动消息权限时降级为文字清单
  - 图库列表每页 30
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
from image_store import ImageStore, MAX_THUMB_BYTES  # noqa: E402

TEST_DIR = r"D:\DSH\qqbot\_test_thumb"
G = "GROUP_X"
failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


def make_png(w, h, color):
    def chunk(tag, data):
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    )


class FakeMessage:
    _n = 0

    def __init__(self, content=""):
        FakeMessage._n += 1
        self.content = content
        self.attachments = []
        self.id = f"m{FakeMessage._n}"
        self.group_openid = G
        self.author = types.SimpleNamespace(member_openid="u", user_openid="u")
        self.replies = []

    async def reply(self, **kw):
        self.replies.append(kw)
        return {"id": "s"}

    @property
    def text(self):
        return "\n".join(r.get("content") or "" for r in self.replies)

    @property
    def images(self):
        return [r for r in self.replies if r.get("msg_type") == 7]


class FakeApi:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def post_group_message(self, **kw):
        if self.fail:
            raise RuntimeError("no active permission")
        self.sent.append(kw)
        return {"id": "m"}

    async def post_c2c_message(self, **kw):
        if self.fail:
            raise RuntimeError("no active permission")
        self.sent.append(kw)
        return {"id": "m"}


class FakeUploader:
    def __init__(self, *a, **k):
        pass

    async def upload_group_image(self, *a, **k):
        return "FAKE"

    async def upload_c2c_image(self, *a, **k):
        return "FAKE"


async def main():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR)

    store = ImageStore(
        db_path=os.path.join(TEST_DIR, "images.db"),
        images_dir=os.path.join(TEST_DIR, "images"),
        thumbs_dir=os.path.join(TEST_DIR, "thumbs"),
    )
    bot.store = store
    bot.MediaUploader = FakeUploader

    def write(name, color, w=64, h=48):
        p = os.path.join(TEST_DIR, name)
        with open(p, "wb") as fh:
            fh.write(make_png(w, h, color))
        return "file:///" + p.replace("\\", "/")

    print("\n[1] 入库生成缩略图（≤100KB）")
    r = await store.add_from_attachment(write("a.png", (255, 0, 0)), ["缩"])
    recs = await store.search_images("缩", group_openid=G)
    thumb = await store.get_thumbnail(recs[0])
    check("缩略图已生成", thumb is not None and os.path.isfile(thumb), str(thumb))
    check("缩略图 ≤100KB", os.path.getsize(thumb) <= MAX_THUMB_BYTES,
          f"{os.path.getsize(thumb)} bytes")

    print("\n[2] 再入库 9 张，凑够两页（每页 8）")
    for i in range(9):
        await store.add_from_attachment(
            write(f"b{i}.png", (i * 25 % 256, 30, 30)), ["缩"]
        )
    check("共 10 张", len(await store.search_images("缩", group_openid=G)) == 10)

    print("\n[3] 拼图：把一页缩略图合成一张")
    thumbs = []
    for rec in (await store.search_images("缩", group_openid=G))[:8]:
        thumbs.append(await store.get_thumbnail(rec))
    from image_store import make_contact_sheet

    sheet = make_contact_sheet(thumbs)
    check("拼图生成成功", sheet is not None and len(sheet) > 0,
          str(len(sheet) if sheet else None))

    print("\n[4] /图库 缩 -> 一条消息里发拼图")
    api = FakeApi()
    m = FakeMessage()
    await bot.do_gallery(m, "/图库 缩", G, api, "group", G)
    check("只发了 1 张（拼图）", len(m.images) == 1, str(len(m.images)))
    check("没有走主动消息", len(api.sent) == 0, str(len(api.sent)))
    check("提示第 1/2 页", "第 1/2 页" in m.text, m.text.splitlines()[0])
    check("提示下一页", "/图库 缩 2" in m.text, m.text)

    print("\n[5] /图库 缩 2 -> 第 2 页拼图")
    api2 = FakeApi()
    m2 = FakeMessage()
    await bot.do_gallery(m2, "/图库 缩 2", G, api2, "group", G)
    check("第 2 页也 1 张拼图", len(m2.images) == 1, str(len(m2.images)))
    check("页码正确", "第 2/2 页" in m2.text, m2.text.splitlines()[0])

    print("\n[6] 拼图失败 -> 降级文字清单")
    real = bot.make_contact_sheet
    bot.make_contact_sheet = lambda *a, **k: None
    try:
        m3 = FakeMessage()
        await bot.do_gallery(m3, "/图库 缩", G, FakeApi(), "group", G)
        check("提示发送失败", "预览图发送失败" in m3.text, m3.text[:60])
        check("给出文字清单", "#" in m3.text, m3.text[:80])
    finally:
        bot.make_contact_sheet = real

    print("\n[7] 图库列表每页 30")
    check("GALLERY_PAGE_SIZE == 30", bot.GALLERY_PAGE_SIZE == 30, str(bot.GALLERY_PAGE_SIZE))

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
