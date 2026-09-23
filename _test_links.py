"""
关键词关联测试：别名解析、存量图片合并、改挂主词、取消关联、查找关联，以及指令层。

覆盖需求：
  - /关联 A B 后，/来只 B 与 /来只 A 等价
  - 关联集合只有一个主关键词，所有别名直连主关键词（单层星型）
  - 取消关联只断开「别名 <-> 主关键词」这条边
  - 可以查找某个关键词的关联关系
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

TEST_DIR = r"D:\DSH\qqbot\_test_links"
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

    def write(name: str, data: bytes) -> str:
        path = os.path.join(TEST_DIR, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return "file:///" + path.replace("\\", "/")

    url_red = write("red.png", make_png(8, 8, (255, 0, 0)))
    url_blue = write("blue.png", make_png(8, 8, (0, 0, 255)))
    url_green = write("green.png", make_png(8, 8, (0, 200, 0)))

    print("\n[1] 基础：建立关联后别名解析到主关键词")
    r_red = await store.add_from_attachment(url_red, ["deepseek"])
    r_blue = await store.add_from_attachment(url_blue, ["大肥鱼"])

    res = await store.link_keywords("deepseek", ["ds", "深度求索"])
    check("返回主关键词 deepseek", res.main == "deepseek", res.main)
    check("两个别名都新增", sorted(res.linked) == ["ds", "深度求索"], str(res.linked))
    check("无图片需要合并", res.images_merged == 0, str(res.images_merged))

    check("resolve(ds)=deepseek", await store.resolve_keyword("ds") == "deepseek")
    check("resolve(DeepSeek) 归一后仍解析",
          await store.resolve_keyword("DeepSeek") == "deepseek")
    check("resolve(无关词) 返回自身", await store.resolve_keyword("无关词") == "无关词")

    print("\n[2] /来只 别名 与 /来只 主 等价")
    hits = set()
    for _ in range(20):
        rec = await store.random_image("ds")
        if rec:
            hits.add(rec.id)
    check("别名 ds 取到主关键词下的图", hits == {r_red.image_id}, f"ids={sorted(hits)}")

    found = await store.search_images("深度求索")
    check("搜索别名返回 1 张", len(found) == 1 and found[0].id == r_red.image_id)

    ids = await store.image_ids_for_keyword("ds")
    check("image_ids_for_keyword 别名生效", ids == [r_red.image_id], str(ids))

    print("\n[3] 用别名入库会沉淀到主关键词")
    r_green = await store.add_from_attachment(url_green, ["ds", "图片"])
    check("入库结果关键词是主词", "deepseek" in r_green.keywords, str(r_green.keywords))
    check("别名不在库里", "ds" not in r_green.keywords, str(r_green.keywords))
    rec_green = await store.get(r_green.image_id)
    check("落库关键词确认", "deepseek" in rec_green.keywords, str(rec_green.keywords))

    print("\n[4] 建立关联时合并存量别名图片")
    r_old = await store.add_from_attachment(url_red, ["旧词"])
    check("重复图关联了新词", "旧词" in r_old.keywords, str(r_old.keywords))
    res2 = await store.link_keywords("新主", ["旧词"])
    check("报告合并了 1 张图", res2.images_merged == 1, str(res2.images_merged))
    rec_old = await store.get(r_red.image_id)
    check("旧词已改为新主", "新主" in rec_old.keywords and "旧词" not in rec_old.keywords,
          str(rec_old.keywords))
    via_alias = await store.random_image("旧词")
    check("别名仍能取到那张图", via_alias is not None and via_alias.id == r_red.image_id)

    print("\n[5] 改挂：主关键词并入另一个集合后保持单层")
    await store.link_keywords("猫", ["猫咪", "喵喵"])
    await store.link_keywords("宠物", ["猫"])
    check("resolve(猫)=宠物", await store.resolve_keyword("猫") == "宠物")
    check("resolve(猫咪)=宠物（无二级串联）", await store.resolve_keyword("猫咪") == "宠物")
    check("resolve(喵喵)=宠物", await store.resolve_keyword("喵喵") == "宠物")

    r_pet = await store.add_from_attachment(url_blue, ["猫咪"])  # 别名，应沉淀到 宠物
    check("别名入库沉淀到主词 宠物", "宠物" in r_pet.keywords, str(r_pet.keywords))

    print("\n[6] 查找关联")
    info = await store.links_for("宠物")
    check("主词查到自己集合", info is not None and info[0] == "宠物", str(info))
    check("集合含全部别名",
          info is not None and sorted(info[1]) == ["喵喵", "猫", "猫咪"], str(info))
    info_alias = await store.links_for("猫咪")
    check("别名也查得到同一个主", info_alias is not None and info_alias[0] == "宠物")
    check("无关联返回 None", await store.links_for("不存在") is None)

    sets = await store.list_link_sets()
    as_dict = dict(sets)
    check("集合列表含 宠物", as_dict.get("宠物") is not None, str(sets))
    check("集合列表含 deepseek 与新主", "deepseek" in as_dict and "新主" in as_dict)

    print("\n[7] 取消关联：只把该词移出集合，集合不受影响")
    u1 = await store.unlink_keyword("猫咪")
    check("别名移出成功", u1.kind == "alias" and u1.main == "宠物", str(u1))
    check("resolve(猫咪) 回到自身", await store.resolve_keyword("猫咪") == "猫咪")
    info_after = await store.links_for("宠物")
    check("集合仍含猫、喵喵",
          info_after is not None and sorted(info_after[1]) == ["喵喵", "猫"], str(info_after))

    u2 = await store.unlink_keyword("不存在")
    check("取消未关联的词返回 missing", u2.kind == "missing")

    u3 = await store.unlink_keyword("宠物")
    check("主词移出后自动改选新主 喵喵",
          u3.kind == "main" and u3.main == "喵喵" and u3.count == 2, str(u3))
    check("集合没有被解散", await store.links_for("喵喵") is not None)
    check("其余成员改挂新主", await store.resolve_keyword("猫") == "喵喵")
    check("新主解析到自己", await store.resolve_keyword("喵喵") == "喵喵")
    check("旧主退化为普通词", await store.resolve_keyword("宠物") == "宠物")

    pool = await store.search_images("喵喵")
    check("图片池随集合迁到新主",
          any(r.id == r_pet.image_id for r in pool), str([r.id for r in pool]))
    check("旧主查不到集合的图", len(await store.search_images("宠物")) == 0)

    print("\n[8] 指令层：/关联 /查找关联 /取消关联")
    # 先给「手机」图库放一张图，否则空集合会被自动清理（符合“删空关键词”预期）
    await store.add_from_attachment(url_green, ["手机"])
    m = FakeMessage("/关联 手机 电话 移动电话")
    await bot.do_link(m, "/关联 手机 电话 移动电话")
    check("关联指令有回复", "已建立关联" in m.text, m.text.splitlines()[0] if m.text else "")
    check("列出别名", "电话" in m.text and "移动电话" in m.text, m.text)

    m2 = FakeMessage("/查找关联 电话")
    await bot.do_find_link(m2, "/查找关联 电话")
    check("查找别名指向主词", "手机" in m2.text and "别名" in m2.text, m2.text)

    m3 = FakeMessage("/查找关联")
    await bot.do_find_link(m3, "/查找关联")
    check("无参列出关联列表", "关联列表" in m3.text, m3.text)

    m4 = FakeMessage("/取消关联 电话")
    await bot.do_unlink(m4, "/取消关联 电话")
    check("取消关联有回复", "断开关联" in m4.text, m4.text)
    check("取消后 resolve 还原", await store.resolve_keyword("电话") == "电话")

    m4b = FakeMessage("/取消关联 手机")
    await bot.do_unlink(m4b, "/取消关联 手机")
    check("移出主词提示自动改选新主", "改选主关键词" in m4b.text, m4b.text)
    check("新主为 移动电话", await store.resolve_keyword("移动电话") == "移动电话")

    m5 = FakeMessage("/关联 只有一个词")
    await bot.do_link(m5, "/关联 只有一个词")
    check("参数不足给用法", "用法" in m5.text, m5.text)

    print("\n[9] 指令分派：handle_command 能识别关联指令")
    m6 = FakeMessage("/关联 车 汽车")
    handled = await bot.handle_command(m6, FakeApi(), "group", "G_TEST", "/关联 车 汽车")
    check("handle_command 返回 True", handled is True)
    check("确实执行了关联，resolve(汽车)=车",
          await store.resolve_keyword("汽车") == "车")

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
