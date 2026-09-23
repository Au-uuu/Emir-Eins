"""图片库存储层测试：去重、关键词关联、随机取图、查询统计。"""

import asyncio
import os
import shutil
import struct
import sys
import zlib

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\DSH\qqbot")

from image_store import ImageStore, normalize_keywords  # noqa: E402

TEST_DIR = r"D:\DSH\qqbot\_test_data"


def make_png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    """不依赖第三方库，手工构造一张纯色 PNG。"""

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


async def main() -> int:
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR)

    store = ImageStore(
        db_path=os.path.join(TEST_DIR, "images.db"),
        images_dir=os.path.join(TEST_DIR, "images"),
    )

    failures = 0

    def check(label: str, ok: bool, extra: str = "") -> None:
        nonlocal failures
        print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
        if not ok:
            failures += 1

    # 造三张不同的图，其中一张准备重复添加
    png_red = make_png(8, 8, (255, 0, 0))
    png_blue = make_png(16, 4, (0, 0, 255))
    png_green = make_png(4, 12, (0, 200, 0))

    # 用本地临时文件冒充下载源（file:// 让 urllib 直接读本地）
    def write(name: str, data: bytes) -> str:
        path = os.path.join(TEST_DIR, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return "file:///" + path.replace("\\", "/")

    url_red = write("red.png", png_red)
    url_blue = write("blue.png", png_blue)
    url_green = write("green.png", png_green)

    print("\n[1] 添加图片并关联关键词")
    r1 = await store.add_from_attachment(
        url_red, ["deepseek", "蓝色大肥鱼"], uploader="user_a"
    )
    check("红色图入库", not r1.duplicate and r1.image_id > 0, f"id={r1.image_id}")
    check("关键词已关联", r1.keywords == ["deepseek", "蓝色大肥鱼"], str(r1.keywords))

    r2 = await store.add_from_attachment(url_blue, ["deepseek"], uploader="user_b")
    check("蓝色图入库", not r2.duplicate, f"id={r2.image_id}")
    r3 = await store.add_from_attachment(url_green, [], uploader="user_c")
    check("绿色图无关键词入库", not r3.duplicate, f"id={r3.image_id}")

    print("\n[2] 哈希去重（同一张图再传一次）")
    r_dup = await store.add_from_attachment(
        url_red, ["新关键词"], uploader="user_d"
    )
    check("判定为重复", r_dup.duplicate, f"id={r_dup.image_id}")
    check("复用同一条记录", r_dup.image_id == r1.image_id)
    check("补充了新关键词", r_dup.newly_linked == ["新关键词"], str(r_dup.newly_linked))
    check(
        "关键词合并正确",
        set(r_dup.keywords) == {"deepseek", "蓝色大肥鱼", "新关键词"},
        str(r_dup.keywords),
    )

    # 同一张图换个文件名/大小写关键词，也应去重
    r_dup2 = await store.add_from_attachment(
        url_red, ["DEEPSEEK"], uploader="user_e"
    )
    check("重复图再次添加仍去重", r_dup2.duplicate and r_dup2.image_id == r1.image_id)
    check("关键词大小写归一后不重复添加", r_dup2.newly_linked == [], str(r_dup2.newly_linked))

    print("\n[3] 实际落盘文件数（应为 3 个，去重不产生新文件）")
    files = []
    for root, _dirs, names in os.walk(store.images_dir):
        files.extend(n for n in names if not n.endswith(".part"))
    check("磁盘文件数 = 3", len(files) == 3, f"实际 {len(files)}: {sorted(files)[:5]}")

    print("\n[4] 随机取图")
    got = await store.random_image()
    check("随机取到图", got is not None, f"#{got.id}" if got else "")
    check("文件真实存在", got is not None and os.path.isfile(got.abs_path))

    hits = set()
    for _ in range(30):
        rec = await store.random_image("deepseek")
        if rec:
            hits.add(rec.id)
    check(
        "关键词 deepseek 下能取到两张图",
        hits == {r1.image_id, r2.image_id},
        f"命中 ids={sorted(hits)}",
    )

    rec_kw = await store.random_image("蓝色大肥鱼")
    check("中文关键词可检索", rec_kw is not None and rec_kw.id == r1.image_id)

    miss = await store.random_image("不存在的词")
    check("不存在的关键词返回 None", miss is None)

    print("\n[5] 查询功能")
    s = await store.stats()
    check("stats.total = 3", s["total"] == 3, str(s))
    check("stats.untagged = 1", s["untagged"] == 1)
    check("stats.hits > 0（取图计数生效）", s["hits"] > 0, f"hits={s['hits']}")

    pairs = await store.list_keywords()
    kws = dict(pairs)
    check("关键词列表含 deepseek=2", kws.get("deepseek") == 2, str(pairs))
    check("关键词列表含 蓝色大肥鱼=1", kws.get("蓝色大肥鱼") == 1)

    found = await store.search_images("deepseek")
    check("搜索 deepseek 返回 2 张", len(found) == 2, f"实际 {len(found)}")

    allrec = await store.search_images(None, limit=10)
    check("无关键词搜索返回全部 3 张", len(allrec) == 3)

    print("\n[6] 尺寸解析")
    rec1 = await store.get(r1.image_id)
    check("PNG 宽高解析正确 (8x8)", (rec1.width, rec1.height) == (8, 8),
          f"{rec1.width}x{rec1.height}")
    rec2 = await store.get(r2.image_id)
    check("PNG 宽高解析正确 (16x4)", (rec2.width, rec2.height) == (16, 4),
          f"{rec2.width}x{rec2.height}")

    print("\n[7] 关键词归一化")
    check(
        "去空/去重/小写/限长",
        normalize_keywords(["  DeepSeek ", "deepseek", "", "  ", "a" * 40])
        == ["deepseek", "a" * 24],
        str(normalize_keywords(["  DeepSeek ", "deepseek", "", "  ", "a" * 40])),
    )

    print("\n[8] 非图片文件应被拒绝")
    txt = os.path.join(TEST_DIR, "notimage.txt")
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write("hello")
    try:
        await store.add_from_attachment(
            "file:///" + txt.replace("\\", "/"), ["x"], content_type="text/plain"
        )
        check("txt 被拒绝", False, "未抛出异常")
    except ValueError as exc:
        check("txt 被拒绝", True, str(exc)[:40])

    print("\n[9] 删除")
    ok = await store.delete(r3.image_id)
    check("删除成功", ok)
    check("删除后总数 2", (await store.stats())["total"] == 2)
    check("删除后文件也被清理", not any("green" in f for f in os.listdir(
        os.path.join(store.images_dir, r3.sha256[:2])
    )))

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
