"""
图库列表测试：/图库 默认输出所有图库（按图片数降序、每个图库最多 2 个关联词），
完整关联词在 /图库 <关键词> 时展示。
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

TEST_DIR = r"D:\DSH\qqbot\_test_gallery"
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


class FakeMessage:
    def __init__(self, content="", msg_id="m1"):
        self.content = content
        self.attachments = []
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

    # 造 6 张各不相同的图
    urls = [
        write(f"p{i}.png", make_png(8, 8 + i, (20 * i % 256, 50, 200)))
        for i in range(1, 7)
    ]

    print("\n[1] 造数据：猫3张、狗2张、鸟1张")
    for u in urls[:3]:
        await store.add_from_attachment(u, ["猫"])
    for u in urls[3:5]:
        await store.add_from_attachment(u, ["狗"])
    await store.add_from_attachment(urls[5], ["鸟"])

    # 给「猫」挂 3 个关联词，验证截断为 2 个
    await store.link_keywords("猫", ["cats", "mao", "kitty"])
    await store.link_keywords("狗", ["dog"])

    print("\n[2] 存储层 list_galleries：按图片数降序、带关联词")
    galleries = await store.list_galleries()
    names = [g[0] for g in galleries]
    counts = {g[0]: g[1] for g in galleries}
    check("顺序 猫>狗>鸟", names == ["猫", "狗", "鸟"], str(names))
    check("数量正确", counts == {"猫": 3, "狗": 2, "鸟": 1}, str(counts))
    cat = dict((g[0], g) for g in galleries)["猫"]
    check("猫 有 3 个关联词", sorted(cat[2]) == ["cats", "kitty", "mao"], str(cat[2]))
    bird = dict((g[0], g) for g in galleries)["鸟"]
    check("鸟 没有关联词", bird[2] == [], str(bird[2]))

    print("\n[3] /图库 默认输出图库列表（用 / 隔开，不换行）")
    m = FakeMessage("/图库")
    await bot.do_gallery(m, "/图库")
    text = m.text
    check("标题为图库列表", "图库列表" in text, text.splitlines()[0] if text else "")
    check("列表不换行（只有标题 + 一行列表）", len(text.splitlines()) == 2, str(text.splitlines()))
    segs = [s.strip() for s in text.split("\n", 1)[1].split(" / ")]
    check("三个图库用 / 隔开", len(segs) == 3, str(segs))
    check("第一段是 猫(3)", segs[0].startswith("猫(3)"), segs[0])
    check("顺序降序", segs[0].startswith("猫") and segs[1].startswith("狗")
          and segs[2].startswith("鸟"), str(segs))
    check("猫的关联词截断为 2 个并带省略号",
          segs[0].count("-") == 2 and segs[0].endswith("…"), segs[0])
    check("狗的关联词只有一个", segs[1] == "狗(2)-dog", segs[1])
    check("鸟没有关联词", segs[2] == "鸟(1)", segs[2])

    print("\n[4] /图库 <关键词> 完整展示关联词")
    m2 = FakeMessage("/图库 猫")
    await bot.do_gallery(m2, "/图库 猫")
    t2 = m2.text
    check("显示了主关键词与全部关联词",
          "主关键词" in t2 and all(a in t2 for a in ("cats", "kitty", "mao")), t2.splitlines()[0])
    check("列出了图片", "共 3 张" in t2, t2)

    m3 = FakeMessage("/图库 kitty")
    await bot.do_gallery(m3, "/图库 kitty")
    check("用关联词查看也能看到主词与整组",
          "主关键词" in m3.text and "cats" in m3.text, m3.text.splitlines()[0])

    m4 = FakeMessage("/图库 不存在")
    await bot.do_gallery(m4, "/图库 不存在")
    check("不存在的图库给提示", "没有图片" in m4.text, m4.text)

    print("\n[5] 空图库")
    empty = ImageStore(
        db_path=os.path.join(TEST_DIR, "empty.db"),
        images_dir=os.path.join(TEST_DIR, "empty"),
    )
    real = bot.store
    bot.store = empty
    m5 = FakeMessage("/图库")
    await bot.do_gallery(m5, "/图库")
    check("空图库给提示", "空" in m5.text, m5.text)
    bot.store = real

    print("\n[6] 分页：/图库 <页码>")
    for i in range(5):
        u = write(f"g{i}.png", make_png(8, 9 + i, ((i * 40) % 256, 10, 10)))
        await store.add_from_attachment(u, [f"页库{i}"])

    saved_size = bot.GALLERY_PAGE_SIZE
    bot.GALLERY_PAGE_SIZE = 2
    try:
        total = await store.gallery_count()
        pages = (total + 2 - 1) // 2
        check("共 8 个图库、4 页", total == 8 and pages == 4, f"total={total} pages={pages}")

        p1 = await bot.build_gallery_list_text(1)
        check("第 1 页不显示页码", not p1.startswith("第"), p1.splitlines()[0])
        check("第 1 页恰好 2 条", len(p1.split(" / ")) == 2, str(p1.split(" / ")))
        check("第 1 页末尾提示下一页", "/图库 2" in p1, p1.splitlines()[-1])

        p2 = await bot.build_gallery_list_text(2)
        check("第 2 页开头显示页码", p2.startswith("第2/4页"), p2.splitlines()[0])
        check("第 2 页恰好 2 条", len(p2.split(" / ")) == 2, str(p2.split(" / ")))

        plast = await bot.build_gallery_list_text(pages)
        check("最后一页提示上一页", f"/图库 {pages - 1}" in plast, plast.splitlines()[-1])

        over = await bot.build_gallery_list_text(pages + 3)
        check("超出页码给提示", "没有第" in over, over)

        # 指令层：/图库 2 翻页
        m6 = FakeMessage("/图库 2")
        await bot.do_gallery(m6, "/图库 2")
        check("指令 /图库 2 走翻页", "第2/" in m6.text, m6.text.splitlines()[0])

        # 单页时不应出现分页提示
        bot.GALLERY_PAGE_SIZE = 999
        single = await bot.build_gallery_list_text(1)
        check("单页无分页提示", "还有更多" not in single and "最后一页" not in single,
              single.splitlines()[-1])
    finally:
        bot.GALLERY_PAGE_SIZE = saved_size

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
