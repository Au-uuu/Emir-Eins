"""
管理员与「最后关键词」保护测试。

覆盖需求：
  - 只有 Bot 管理员可以删掉关键词集合仅剩的最后一个词
  - 普通用户删除会被拒绝
  - 管理员通过「私聊 + 口令」自助登记（QQ 官方机器人拿不到 QQ 号，只能按 openid）
  - 管理员登记指令只在私聊生效，群里命中保持沉默
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

TEST_DIR = r"D:\DSH\qqbot\_test_admin"
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
    def __init__(self, content="", user_openid="user_x", msg_id="m1"):
        self.content = content
        self.attachments = []
        self.id = msg_id
        self.group_openid = "G_TEST"
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
    bot.ADMIN_PASSWORD = "testpw-0000"

    def write(name: str, color) -> str:
        path = os.path.join(TEST_DIR, name)
        with open(path, "wb") as fh:
            fh.write(make_png(8, 8, color))
        return "file:///" + path.replace("\\", "/")

    url_a = write("a.png", (255, 0, 0))
    url_b = write("b.png", (0, 0, 255))
    url_c = write("c.png", (0, 200, 0))
    url_d = write("d.png", (250, 250, 0))

    print("\n[1] 管理员表基础操作")
    check("初始非管理员", not await store.is_admin("nobody"))
    check("登记成功", await store.add_admin("nobody"))
    check("已是管理员", await store.is_admin("nobody"))
    check("重复登记返回 False", not await store.add_admin("nobody"))
    check("空 openid 不登记", not await store.add_admin(""))

    print("\n[2] 私聊口令登记")
    m = FakeMessage("管理员 000000", user_openid="u1")
    await bot.do_admin_setup(m, "管理员 000000")
    check("口令错误有提示", "口令错误" in m.text, m.text)
    check("错误口令不登记", not await store.is_admin("u1"))

    m2 = FakeMessage("管理员 testpw-0000", user_openid="u2")
    await bot.do_admin_setup(m2, "管理员 testpw-0000")
    check("正确口令登记成功", "登记成功" in m2.text, m2.text)
    check("u2 成为管理员", await store.is_admin("u2"))

    m3 = FakeMessage("管理员", user_openid="u3")
    await bot.do_admin_setup(m3, "管理员")
    check("没有口令提示用法", "用法" in m3.text, m3.text)

    m4 = FakeMessage("admin testpw-0000", user_openid="u4")
    await bot.do_admin_setup(m4, "admin testpw-0000")
    check("英文 admin 也可用", await store.is_admin("u4"))

    print("\n[3] 指令分派：只在私聊生效，群里沉默")
    m5 = FakeMessage("管理员 testpw-0000", user_openid="u5")
    handled = await bot.handle_command(m5, FakeApi(), "c2c", "u5", "管理员 testpw-0000")
    check("私聊被处理", handled is True)
    check("私聊登记成功", await store.is_admin("u5"))

    m6 = FakeMessage("管理员 testpw-0000", user_openid="u6")
    handled6 = await bot.handle_command(m6, FakeApi(), "group", "G", "管理员 testpw-0000")
    check("群聊返回已处理（不回落到聊天）", handled6 is True)
    check("群聊保持沉默", m6.replies == [], str(m6.replies))
    check("群聊不登记", not await store.is_admin("u6"))

    print("\n[4] 存储层：最后一个词普通用户不能删")
    r = await store.add_from_attachment(url_a, ["独占库"])
    rec = await store.get(r.image_id)

    res = await store.unlink_keyword("独占库")  # allow_last 默认 False
    check("被拒绝 protected_last", res.kind == "protected_last", res.kind)
    check("图库仍在", len(await store.search_images("独占库")) == 1)
    check("文件仍在", os.path.isfile(rec.abs_path))

    res_admin = await store.unlink_keyword("独占库", allow_last=True)
    check("管理员可删 gallery_deleted", res_admin.kind == "gallery_deleted", res_admin.kind)
    check("报告影响 1 张图", res_admin.count == 1, str(res_admin.count))
    check("孤儿文件被清理", res_admin.orphan_removed and not os.path.exists(rec.abs_path))
    check("图库已消失", len(await store.search_images("独占库")) == 0)

    print("\n[5] 集合从 2 个减到 1 个允许，删最后 1 个被拒")
    await store.add_from_attachment(url_b, ["甲"])
    await store.link_keywords("甲", ["乙"])
    u = await store.unlink_keyword("乙")
    check("非管理员可删到只剩 1 个", u.kind == "alias", u.kind)
    check("甲 仍是图库", len(await store.search_images("甲")) == 1)
    u2 = await store.unlink_keyword("甲")
    check("删最后一个被拒", u2.kind == "protected_last", u2.kind)

    print("\n[6] 指令层：普通用户被拒、管理员可删")
    await store.add_from_attachment(url_c, ["锁2"])

    m7 = FakeMessage("/取消关联 锁2", user_openid="stranger")
    await bot.do_unlink(m7, "/取消关联 锁2")
    check("普通用户收到拒绝提示", "最后一个词" in m7.text, m7.text)
    check("图库未被删", len(await store.search_images("锁2")) == 1)

    boss = FakeMessage("/取消关联 锁2", user_openid="boss")
    await bot.do_admin_setup(boss, "管理员 testpw-0000")  # boss 自助登记
    m8 = FakeMessage("/取消关联 锁2", user_openid="boss")
    await bot.do_unlink(m8, "/取消关联 锁2")
    check("管理员成功删除图库", "已删除图库" in m8.text, m8.text)
    check("图库已被删", len(await store.search_images("锁2")) == 0)

    print("\n[7] 未启用口令时不开放登记")
    saved = bot.ADMIN_PASSWORD
    bot.ADMIN_PASSWORD = ""
    m9 = FakeMessage("管理员 testpw-0000", user_openid="u9")
    await bot.do_admin_setup(m9, "管理员 testpw-0000")
    check("提示未启用", "未启用" in m9.text, m9.text)
    check("不会登记", not await store.is_admin("u9"))
    bot.ADMIN_PASSWORD = saved

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
