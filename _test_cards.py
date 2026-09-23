"""
游戏名片测试。

  - /添加名片 <备注>：引用图片入库为名片；同一备注重复添加失败
  - /游戏名片：拼成一张竖排图（宽 960）发送
  - /删除名片 <备注> / <编号> / 引用图片（指纹）
  - 原图保存，删名片后孤儿图片与缩略图清理
"""

import asyncio
import io
import os
import shutil
import struct
import sys
import types
import zlib

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\DSH\qqbot")

import bot  # noqa: E402
from image_store import ImageStore, make_card_sheet  # noqa: E402

TEST_DIR = r"D:\DSH\qqbot\_test_cards"
OWNER = "USER_OPENID_A"
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


class FakeAttachment:
    def __init__(self, url, content_type="image/png"):
        self.url = url
        self.filename = os.path.basename(url)
        self.content_type = content_type


class FakeMessage:
    _n = 0

    def __init__(self, content="", attachments=None, openid=OWNER):
        FakeMessage._n += 1
        self.content = content
        self.attachments = attachments or []
        self.id = f"m{FakeMessage._n}"
        self.group_openid = "G"
        self.author = types.SimpleNamespace(
            user_openid=openid, member_openid=openid
        )
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
    async def post_group_message(self, **kw):
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

    def write(name, color, w=1200, h=800):
        p = os.path.join(TEST_DIR, name)
        with open(p, "wb") as fh:
            fh.write(make_png(w, h, color))
        return "file:///" + p.replace("\\", "/")

    u1 = write("a.png", (200, 0, 0))
    u2 = write("b.png", (0, 200, 0))

    print("\n[1] 添加名片")
    c1 = await store.add_card(u1, OWNER, "王者荣耀")
    check("返回名片", c1.remark == "王者荣耀" and c1.sha256, str(c1))
    check("原图存在", os.path.isfile(c1.abs_path))
    cards = await store.list_cards(OWNER)
    check("列表 1 张", len(cards) == 1, str(len(cards)))

    print("\n[2] 同一备注重复 -> 失败")
    try:
        await store.add_card(u2, OWNER, "王者荣耀")
        check("重复备注被拒", False, "未抛异常")
    except ValueError as exc:
        check("重复备注被拒", "已存在" in str(exc), str(exc))

    c2 = await store.add_card(u2, OWNER, "原神")
    check("不同备注可加", len(await store.list_cards(OWNER)) == 2)

    print("\n[3] 指令层：/添加名片 重复备注提示失败")
    m = FakeMessage(
        "/添加名片 王者荣耀", attachments=[FakeAttachment(u1)]
    )
    await bot.do_add_card(m, FakeApi(), "group", "G", "/添加名片 王者荣耀")
    check("提示添加失败", "添加失败" in m.text and "已存在" in m.text, m.text)

    m2 = FakeMessage("/添加名片 崩铁", attachments=[FakeAttachment(u1)])
    await bot.do_add_card(m2, FakeApi(), "group", "G", "/添加名片 崩铁")
    check("新备注成功", "名片添加成功" in m2.text, m2.text)

    print("\n[3b] 纯数字备注 / content_type 缺失兜底")
    mn = FakeMessage("/添加名片 123", attachments=[FakeAttachment(u1)])
    await bot.do_add_card(mn, FakeApi(), "group", "G", "/添加名片 123")
    check("纯数字备注提示失败", "添加失败" in mn.text and "纯数字" in mn.text, mn.text)
    check("缺 content_type 看扩展名识别",
          bot._looks_like_image("", "a.jpg", "http://x/a.jpg"),
          "")
    check("缺 content_type 看 URL 识别",
          bot._looks_like_image(None, None, "http://x/a.png?sign=1"), "")

    print("\n[4] 拼图：宽 1920、竖排")
    triples = [(i + 1, c.remark, c.abs_path) for i, c in enumerate(await store.list_cards(OWNER))]
    data, included = make_card_sheet(triples)
    check("生成拼图", data is not None and included == 3, f"inc={included}")
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    check("宽度 1920", im.width == 1920, str(im.width))
    check("竖向（高度大于宽）", im.height > im.width, f"{im.width}x{im.height}")

    print("\n[5] /游戏名片 -> 发一张拼图")
    api = FakeApi()
    mg = FakeMessage()
    await bot.do_game_card(mg, api, "group", "G", "/游戏名片")
    check("发了 1 张图", len(mg.images) == 1, str(len(mg.images)))
    check("汇总提到张数", "共 3 张" in mg.text, mg.text)

    print("\n[6] 删除：按备注 / 编号 / 指纹")
    md = FakeMessage()
    await bot.do_delete_card(md, api, "group", "G", "/删除名片 原神")
    check("按备注删除", "已删除名片：原神" in md.text, md.text)
    check("剩 2 张", len(await store.list_cards(OWNER)) == 2)

    mi = FakeMessage()
    await bot.do_delete_card(mi, api, "group", "G", "/删除名片 1")
    check("按编号删除", "已删除名片" in mi.text, mi.text)
    check("剩 1 张", len(await store.list_cards(OWNER)) == 1)

    # 指纹删除：先加回一张，再用引用图片删
    await store.add_card(u2, OWNER, "回到原神")
    mf = FakeMessage("/删除名片", attachments=[FakeAttachment(u2)])
    await bot.do_delete_card(mf, api, "group", "G", "/删除名片")
    check("按指纹删除", "已删除名片：回到原神" in mf.text, mf.text)
    check("剩 1 张", len(await store.list_cards(OWNER)) == 1)

    print("\n[7] 删除不存在 / 别人的名片")
    mn = FakeMessage()
    await bot.do_delete_card(mn, api, "group", "G", "/删除名片 不存在")
    check("提示没找到", "没找到" in mn.text, mn.text)
    other = FakeMessage(openid="OTHER_USER")
    await bot.do_delete_card(other, api, "group", "G", "/删除名片 崩铁")
    check("删不到别人的", "没找到" in other.text, other.text)
    check("持有人的还在", len(await store.list_cards(OWNER)) == 1)

    print("\n[8] 孤儿清理：删最后一张后文件+缩略图清理")
    last = (await store.list_cards(OWNER))[0]
    mlast = FakeMessage()
    await bot.do_delete_card(mlast, api, "group", "G", f"/删除名片 {last.remark}")
    check("已清空", len(await store.list_cards(OWNER)) == 0)
    check("原图已删", not os.path.isfile(last.abs_path))
    check("缩略图已删", not os.path.exists(store._thumb_path(last.sha256)))

    print("\n[9] handle_command 分派（不被 /添加、/删除 抢先）")
    mh = FakeMessage("/添加名片 测试")
    await bot.handle_command(mh, None, "group", "G", "/添加名片 测试")
    check("/添加名片 未被 /添加 抢走", "引用" in mh.text, mh.text)
    mh2 = FakeMessage("/删除名片")
    await bot.handle_command(mh2, None, "group", "G", "/删除名片")
    check("/删除名片 未被 /删除 抢走", "用法" in mh2.text, mh2.text)

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
