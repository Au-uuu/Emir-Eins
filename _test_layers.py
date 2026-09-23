"""
本批新功能测试：

  - 取图/删除的层参数：私有 / 公开 / 全部
  - /删除图库（管理员）：列出并删除某图库层
  - 图库名不能是纯数字
  - /批量添加 一次最多 5 张
  - /私有 群聊不接受第二个参数
  - /查询标识（原 /id 改名）
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

TEST_DIR = r"D:\DSH\qqbot\_test_layers"
G_A = "GROUP_A_OPENID"
G_B = "GROUP_B_OPENID"
failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


def make_png(width, height, color):
    def chunk(tag, data):
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


class FakeMessage:
    _seq = 0

    def __init__(self, content="", attachments=None, user_openid="u"):
        FakeMessage._seq += 1
        self.content = content
        self.attachments = attachments or []
        self.id = f"m{FakeMessage._seq}"
        self.group_openid = G_A
        self.author = types.SimpleNamespace(
            user_openid=user_openid, member_openid=user_openid
        )
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}

    @property
    def text(self):
        return "\n".join(r.get("content") or "" for r in self.replies)

    @property
    def images(self):
        return [r for r in self.replies if r.get("msg_type") == 7]


class FakeApi:
    async def post_group_message(self, **kwargs):
        return {"id": "m"}


class FakeUploader:
    def __init__(self, *a, **k):
        pass

    async def upload_group_image(self, group_openid, path, name=None):
        return "FAKE"

    async def upload_c2c_image(self, openid, path, name=None):
        return "FAKE"


async def main() -> int:
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR)

    store = ImageStore(
        db_path=os.path.join(TEST_DIR, "images.db"),
        images_dir=os.path.join(TEST_DIR, "images"),
    )
    bot.store = store
    bot.MediaUploader = FakeUploader

    def write(name, color):
        p = os.path.join(TEST_DIR, name)
        with open(p, "wb") as fh:
            fh.write(make_png(8, 8, color))
        return "file:///" + p.replace("\\", "/")

    ua = write("a.png", (255, 0, 0))
    ub = write("b.png", (0, 0, 255))
    uc = write("c.png", (0, 200, 0))

    print("\n[1] 造数据：双(A私有) + 公开")
    ra = await store.add_from_attachment(ua, ["双"])
    await store.set_gallery_private("双", G_A)  # (双,A) <- a
    rb = await store.add_from_attachment(ub, ["双"], group_openid=G_B)  # public b
    check("私有层 1 张、公开层 1 张",
          len(await store.search_images("双", group_openid=G_A)) == 2)

    print("\n[2] 存储层：层选择")
    rec_p = await store.random_image("双", G_A, "private")
    rec_pub = await store.random_image("双", G_A, "public")
    check("私有层取到 a", rec_p is not None and rec_p.id == ra.image_id)
    check("公开层取到 b", rec_pub is not None and rec_pub.id == rb.image_id)
    got = { (await store.random_image("双", G_A, "all")).id for _ in range(20) }
    check("全部层能取到两张", got == {ra.image_id, rb.image_id}, str(got))
    check("B 群私有层为空", await store.random_image("双", G_B, "private") is None)
    check("B 群公开层有 b", (await store.random_image("双", G_B, "public")).id == rb.image_id)
    check("单聊取私有层为空（回归）", await store.random_image("双", None, "private") is None)
    check("单聊取公开层有图", await store.random_image("双", None, "public") is not None)

    print("\n[3] /来只 层参数")
    m = FakeMessage("/来只 双 私有")
    await bot.do_random_image(m, FakeApi(), "group", G_A, "/来只 双 私有")
    check("私有层发了图", len(m.images) == 1, str(m.replies))
    m2 = FakeMessage("/来只 双 公开")
    await bot.do_random_image(m2, FakeApi(), "group", G_A, "/来只 双 公开")
    check("公开层发了图", len(m2.images) == 1)
    m3 = FakeMessage("/来只 双 私有")
    await bot.do_random_image(m3, FakeApi(), "group", G_B, "/来只 双 私有")
    check("B 群取私有层给文字提示", len(m3.images) == 0 and "私有" in m3.text, m3.text)

    print("\n[4] /删除 层参数")
    md = FakeMessage("/删除 双 私有", attachments=[FakeAttachment(ua)])
    await bot.do_delete_image(md, None, "group", G_A, "/删除 双 私有")
    check("删私有层成功", "已从图库" in md.text, md.text)
    check("公开层还在", len(await store.search_images("双", group_openid=G_B)) == 1)
    md2 = FakeMessage("/删除 双 公开", attachments=[FakeAttachment(ub)])
    await bot.do_delete_image(md2, None, "group", G_A, "/删除 双 公开")
    check("删公开层成功", "已从图库" in md2.text, md2.text)
    check("双已彻底没有图", len(await store.search_images("双", group_openid=G_A)) == 0)

    print("\n[5] /删除图库（管理员）")
    await store.add_from_attachment(uc, ["层库"])
    m_no = FakeMessage("/删除图库")
    await bot.do_delete_gallery(m_no, "/删除图库")
    check("非管理员被拒", "只有 Bot 管理员" in m_no.text, m_no.text)

    await store.add_admin("u")
    m_list = FakeMessage("/删除图库")
    await bot.do_delete_gallery(m_list, "/删除图库")
    check("管理员可列出图库层", "→" in m_list.text and "层库" in m_list.text, m_list.text)

    m_del = FakeMessage("/删除图库 层库 公开")
    await bot.do_delete_gallery(m_del, "/删除图库 层库 公开")
    check("删除公开层", "已删除图库层" in m_del.text, m_del.text)
    check("层库已空", len(await store.search_images("层库")) == 0)

    print("\n[6] 图库名不能是纯数字")
    try:
        await store.add_from_attachment(ua, ["123"])
        check("纯数字被拒", False, "未抛异常")
    except ValueError as exc:
        check("纯数字被拒", "纯数字" in str(exc), str(exc))
    try:
        await store.link_keywords("12", ["x"])
        check("关联纯数字主词被拒", False)
    except ValueError as exc:
        check("关联纯数字主词被拒", "纯数字" in str(exc), str(exc))

    print("\n[7] /批量添加 一次最多 5 张")
    urls = [write(f"n{i}.png", (i * 30 % 256, 20, 20)) for i in range(6)]
    mb = FakeMessage(
        "/批量添加 批批", attachments=[FakeAttachment(u) for u in urls]
    )
    await bot.do_batch_add(mb, None, "group", G_A, "/批量添加 批批")
    check("6 张被拒绝", "最多" in mb.text, mb.text)
    check("没有入库", len(await store.search_images("批批")) == 0)

    print("\n[8] 群聊 /私有 不接受第二个参数")
    await store.add_from_attachment(ua, ["参测"])
    mp = FakeMessage("/私有 参测 " + G_B)
    await bot.do_set_private(mp, "group", G_A, "/私有 参测 " + G_B)
    check("第二个参数被拒", "不能带第二个参数" in mp.text, mp.text)

    print("\n[9] /查询标识")
    mid = FakeMessage("/查询标识")
    await bot.do_whoami(mid, "group", G_A)
    check("查询标识返回 openid", "u" in mid.text and G_A in mid.text, mid.text)
    handled = await bot.handle_command(
        FakeMessage("/查询标识"), None, "group", G_A, "/查询标识"
    )
    check("handle_command 识别 /查询标识", handled is True)
    old = await bot.handle_command(FakeMessage("/id"), None, "group", G_A, "/id")
    check("旧 /id 不再识别", old is False, str(old))

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
