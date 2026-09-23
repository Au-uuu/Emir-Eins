"""
删除图片测试：从指定图库移除图片、多图库共享同一张图、孤儿文件清理、空关键词清理。

覆盖需求：
  - 引用图片后 `/删除 图库名称`（斜杠可省）
  - 先检查图库是否存在，再检查图库里有没有这张图的指纹
  - 图库只存指纹索引，同一张图可被多个图库存放；删除只解除与指定图库的关联
  - 不再被任何图库引用时才清理图片文件
  - 关键词删空后清理空关联
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

TEST_DIR = r"D:\DSH\qqbot\_test_delete"
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
        self.filename = "x.png"
        self.content_type = content_type
        self.width = None
        self.height = None
        self.size = None


class FakeMessage:
    def __init__(self, content="", attachment=None, msg_id="m1"):
        self.content = content
        self.attachments = [attachment] if attachment else []
        self.id = msg_id
        self.group_openid = "G_TEST"
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


async def main() -> int:
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR)

    store = ImageStore(
        db_path=os.path.join(TEST_DIR, "images.db"),
        images_dir=os.path.join(TEST_DIR, "images"),
    )
    bot.store = store

    def write(name: str, data: bytes) -> str:
        path = os.path.join(TEST_DIR, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return "file:///" + path.replace("\\", "/")

    url_red = write("red.png", make_png(8, 8, (255, 0, 0)))
    url_blue = write("blue.png", make_png(8, 8, (0, 0, 255)))
    url_green = write("green.png", make_png(8, 8, (0, 200, 0)))
    url_new = write("new.png", make_png(8, 8, (250, 250, 0)))

    print("\n[1] 一张图可被多个图库存放")
    r_red = await store.add_from_attachment(url_red, ["图库A", "图库B"])
    check("红图入库并挂两个图库",
          set(r_red.keywords) == {"图库a", "图库b"}, str(r_red.keywords))
    check("图库A 能搜到", len(await store.search_images("图库A")) == 1)
    check("图库B 能搜到", len(await store.search_images("图库B")) == 1)

    print("\n[2] 从图库A删除：只解除关联，图仍在图库B")
    rec = await store.get(r_red.image_id)
    red_path = rec.abs_path

    res = await store.delete_from_gallery(url_red, "图库A")
    check("状态 deleted", res.status == "deleted", res.status)
    check("图库名正确（关键词统一小写）", res.keyword == "图库a", res.keyword)
    check("图库A 已没有图片", res.gallery_emptied)
    check("图片仍被图库B引用，文件未删", not res.orphan_removed and os.path.isfile(red_path))
    check("图库A 搜不到", len(await store.search_images("图库A")) == 0)
    check("图库B 仍搜得到", len(await store.search_images("图库B")) == 1)
    check("图片记录还在", await store.get(r_red.image_id) is not None)

    print("\n[3] 从最后一个图库删除：连文件一起清理")
    res2 = await store.delete_from_gallery(url_red, "图库B")
    check("状态 deleted", res2.status == "deleted", res2.status)
    check("标记为孤儿清理", res2.orphan_removed)
    check("图片记录已删", await store.get(r_red.image_id) is None)
    check("磁盘文件已清理", not os.path.exists(red_path))

    print("\n[4] 图库不存在 / 图片不在该图库 / 图库里根本没这张图")
    r_blue = await store.add_from_attachment(url_blue, ["图库C"])
    r_green = await store.add_from_attachment(url_green, ["图库D"])

    miss = await store.delete_from_gallery(url_blue, "不存在的图库")
    check("图库不存在 -> gallery_missing", miss.status == "gallery_missing", miss.status)

    not_in = await store.delete_from_gallery(url_green, "图库C")
    check("图不在该图库 -> not_in_gallery", not_in.status == "not_in_gallery", not_in.status)
    check("未误删绿图", await store.get(r_green.image_id) is not None)

    unknown = await store.delete_from_gallery(url_new, "图库C")
    check("库里没这张图 -> image_unknown", unknown.status == "image_unknown", unknown.status)

    print("\n[5] 用别名删除图库")
    await store.link_keywords("车", ["汽车", "vehicle"])
    r_car = await store.add_from_attachment(url_blue, ["汽车"])  # 解析到主词「车」
    check("别名入库沉淀到主词", "车" in r_car.keywords, str(r_car.keywords))
    car_res = await store.delete_from_gallery(url_blue, "vehicle")
    check("别名删除解析到主词", car_res.keyword == "车", car_res.keyword)
    check("删除成功", car_res.status == "deleted", car_res.status)

    print("\n[6] 关键词删空后清理空关联")
    await store.link_keywords("宠物", ["猫", "喵"])
    r_pet = await store.add_from_attachment(url_green, ["宠物"])
    check("集合有图", len(await store.search_images("宠物")) == 1)
    pet_res = await store.delete_from_gallery(url_green, "宠物")
    check("删空图库", pet_res.gallery_emptied and pet_res.status == "deleted")
    check("空集合已清理", await store.links_for("宠物") is None)
    check("空关联计数 >= 1", pet_res.purged >= 1, str(pet_res.purged))

    print("\n[7] 指令层：/删除")
    r2 = await store.add_from_attachment(url_new, ["指令库"])
    m = FakeMessage("/删除 指令库", attachment=FakeAttachment(url_new))
    await bot.do_delete_image(m, FakeApi(), "group", "G", "/删除 指令库")
    check("删除成功有回复", "已从图库" in m.text, m.text)
    check("确实删掉了", len(await store.search_images("指令库")) == 0)

    m2 = FakeMessage("/删除 不存在库", attachment=FakeAttachment(url_blue))
    await bot.do_delete_image(m2, FakeApi(), "group", "G", "/删除 不存在库")
    check("图库不存在有提示", "不存在" in m2.text, m2.text)

    m3 = FakeMessage("/删除 图库C")  # 没引用图片
    await bot.do_delete_image(m3, FakeApi(), "group", "G", "/删除 图库C")
    check("没引用图片提示用法", "用法" in m3.text, m3.text)

    m4 = FakeMessage(
        "/删除", attachment=FakeAttachment(url_green)
    )  # 没给图库名
    await bot.do_delete_image(m4, FakeApi(), "group", "G", "/删除")
    check("没给图库名提示用法", "用法" in m4.text, m4.text)

    m5 = FakeMessage("删除 图库C", attachment=FakeAttachment(url_blue))
    handled = await bot.handle_command(m5, FakeApi(), "group", "G", "删除 图库C")
    check("handle_command 识别无斜杠删除", handled is True)
    check("无斜杠也能删", "已从图库" in m5.text, m5.text)

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
