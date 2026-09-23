"""
群私有图库（分层模型）测试。

模型：图库 = (关键词, owner_group)，owner_group='' 为公开层，否则为某群私有层。
  - /私有 猫（群A）：把公开层图片全部移入 (猫,A)，之后 A 的 /添加 猫 进 (猫,A)
  - 群B 仍可 /添加 猫 -> 公开层；A 的 /图库 同时显示 猫 和 猫[私有]
  - /删除 猫：私有层优先，没有再删公开层
  - /公开 猫：把 (猫,A) 并回公开层
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

TEST_DIR = r"D:\DSH\qqbot\_test_privacy"
G_A = "GROUP_A_OPENID"
G_B = "GROUP_B_OPENID"
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
    def __init__(self, content="", user_openid="u", msg_id="m1"):
        self.content = content
        self.attachments = []
        self.id = msg_id
        self.group_openid = G_A
        self.author = types.SimpleNamespace(
            user_openid=user_openid, member_openid=user_openid
        )
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

    u1 = write("a.png", (255, 0, 0))
    u2 = write("b.png", (0, 0, 255))
    u3 = write("c.png", (0, 200, 0))
    u4 = write("d.png", (250, 250, 0))

    async def vis(kw, group):
        return await store.search_images(kw, group_openid=group)

    print("\n[1] 公开层：所有群都能看到")
    r1 = await store.add_from_attachment(u1, ["秘"])
    check("A 群看得到", len(await vis("秘", G_A)) == 1)
    check("B 群也看得到", len(await vis("秘", G_B)) == 1)
    check("单聊也看得到", len(await vis("秘", None)) == 1)

    print("\n[2] 群A /私有：公开层图片全部移入 (秘,A)")
    check("返回图库名", await store.set_gallery_private("秘", G_A) == "秘")
    check("私有标记存在", ("秘", G_A) in await store.gallery_privacy_map())
    check("A 群仍能取到（1 张）", len(await vis("秘", G_A)) == 1)
    check("B 群取不到", len(await vis("秘", G_B)) == 0)
    check("单聊取不到", len(await vis("秘", None)) == 0)
    check("A 群随机能抽到", await store.random_image("秘", G_A) is not None)
    check("B 群随机抽不到", await store.random_image("秘", G_B) is None)
    check("A 群该图可见", await store.is_gallery_private("秘", G_A))
    check("B 群未私有", not await store.is_gallery_private("秘", G_B))

    print("\n[3] 群B /添加 同名图库 -> 进公开层")
    await store.add_from_attachment(u2, ["秘"], group_openid=G_B)
    check("B 群取到公开层（1 张）", len(await vis("秘", G_B)) == 1)
    check("A 群同时看到两层（2 张）", len(await vis("秘", G_A)) == 2)

    print("\n[4] /图库 列表：同名公开层 + 私有层各一条")
    galleries = await store.list_galleries(group_openid=G_A)
    entries = sorted((name, owner) for name, _c, _a, owner in galleries if name == "秘")
    check("A 群看到两个条目", entries == [("秘", ""), ("秘", G_A)], str(entries))
    gallery_b = await store.list_galleries(group_openid=G_B)
    entries_b = [(name, owner) for name, _c, _a, owner in gallery_b if name == "秘"]
    check("B 群只看到公开层", entries_b == [("秘", "")], str(entries_b))

    print("\n[5] /删除：私有层优先")
    res = await store.delete_from_gallery(u1, "秘", group_openid=G_A)
    check("删除成功", res.status == "deleted", res.status)
    check("A 群只剩公开层 1 张", len(await vis("秘", G_A)) == 1)
    check("原私有图已成了孤儿被清理", await store.get(r1.image_id) is None)

    print("\n[6] /公开：私有层并回公开层")
    await store.add_from_attachment(u3, ["秘"], group_openid=G_A)  # 进 (秘,A)
    check("A 私有层有一点内容", len(await vis("秘", G_A)) == 2)
    check("公开层仍只有 B 的 1 张", len(await vis("秘", G_B)) == 1)
    check("公开返回图库名", await store.set_gallery_public("秘", G_A) == "秘")
    check("私有标记已清除", ("秘", G_A) not in await store.gallery_privacy_map())
    check("A 群仍 2 张", len(await vis("秘", G_A)) == 2)
    check("B 群现在也 2 张（并回公开）", len(await vis("秘", G_B)) == 2)

    print("\n[7] 别名解析到主词")
    await store.add_from_attachment(u4, ["主图"])
    await store.link_keywords("主图", ["别名"])
    await store.set_gallery_private("别名", G_B)
    check("按别名私有 -> 标记在主词上", ("主图", G_B) in await store.gallery_privacy_map())
    check("A 群看不到主图", len(await vis("主图", G_A)) == 0)
    check("B 群看得到主图", len(await vis("主图", G_B)) == 1)

    print("\n[8] 指令层")
    m = FakeMessage("/私有 主图")
    await bot.do_set_private(m, "group", G_A, "/私有 主图")
    check("群聊 /私有 成功", "已设为当前群私有" in m.text, m.text)

    m2 = FakeMessage("/公开 主图")
    await bot.do_set_public(m2, "group", G_A, "/公开 主图")
    check("群聊 /公开 成功", "已恢复公开" in m2.text, m2.text)

    m3 = FakeMessage("/私有 不存在")
    await bot.do_set_private(m3, "group", G_A, "/私有 不存在")
    check("不存在给提示", "不存在" in m3.text, m3.text)

    m4 = FakeMessage("/公开 主图")
    await bot.do_set_public(m4, "c2c", "openid", "/公开 主图")
    check("私聊 /公开 缺群 id 给用法", "群聊openid" in m4.text, m4.text)

    print("\n[9] 管理员私聊按群 openid 设私有")
    mn = FakeMessage("/私有 主图 " + G_B)
    await bot.do_set_private(mn, "c2c", "u", "/私有 主图 " + G_B)
    check("非管理员被拒", "只有管理员" in mn.text, mn.text)

    await store.add_admin("u")
    ma = FakeMessage("/私有 主图 " + G_B)
    await bot.do_set_private(ma, "c2c", "u", "/私有 主图 " + G_B)
    check("管理员可远程设私有", "已设为群" in ma.text, ma.text)
    check("标记已写入", ("主图", G_B) in await store.gallery_privacy_map())

    ml = FakeMessage("/私有")
    await bot.do_set_private(ml, "c2c", "u", "/私有")
    check("管理员可列出绑定", "主图" in ml.text and G_B in ml.text, ml.text)

    print("\n[10] /图库 默认页按群过滤（回归）")
    cmd = FakeMessage("/图库")
    await bot.handle_command(cmd, None, "group", G_B, "/图库")
    check("B 群默认页用 name[数量] 标私有", "主图[" in cmd.text, cmd.text)
    cmd2 = FakeMessage("/图库")
    await bot.handle_command(cmd2, None, "group", G_A, "/图库")
    check("A 群默认页看不到 B 的私有层", "主图[" not in cmd2.text, cmd2.text)

    print("\n[11] 改主/删除时私有层与标记跟随")
    u5 = write("e.png", (10, 10, 10))
    await store.add_from_attachment(u5, ["换主"])
    await store.link_keywords("换主", ["换主别名"])
    await store.set_gallery_private("换主", G_B)  # 公开层 -> (换主,B) + 标记
    check("设私有后层在 B", len(await vis("换主", G_B)) == 1)
    check("标记在旧主", ("换主", G_B) in await store.gallery_privacy_map())

    r = await store.unlink_keyword("换主")  # 主词移出 -> 新主 换主别名
    check("改主到 换主别名", r.kind == "main" and r.main == "换主别名", str(r))
    check("私有层跟到新主（B 群仍可见）", len(await vis("换主别名", G_B)) == 1)
    check("标记跟到新主", ("换主别名", G_B) in await store.gallery_privacy_map())
    check("旧主不再有可见层", len(await vis("换主", G_A)) == 0)

    u6 = write("f.png", (200, 200, 10))
    await store.add_from_attachment(u6, ["换主别名"], group_openid=G_B)
    check("B 群新增进私有层", len(await vis("换主别名", G_B)) == 2)
    check("A 群看不到私有新增", len(await vis("换主别名", G_A)) == 0)

    dele = await store.unlink_keyword("换主别名", allow_last=True)
    check("管理员删除图库", dele.kind == "gallery_deleted", dele.kind)
    check("私有标记一并清除",
          ("换主别名", G_B) not in await store.gallery_privacy_map())

    print("\n[12] /id 查询")
    mp = FakeMessage("/id")
    await bot.do_whoami(mp, "group", G_A)
    check("返回用户 openid", "u" in mp.text and G_A in mp.text, mp.text)

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
