# -*- coding: utf-8 -*-
"""「先发图，再发添加」兜底测试（不联网、不写图库）。"""

import asyncio
import os
import sys
import tempfile
import types

os.environ["QQ_BOT_GROUP_CONTEXT"] = "0"
os.environ["QQ_BOT_MEMORY"] = "0"
os.environ["QQ_BOT_RANDOM_REPLY"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


class Att:
    def __init__(self, url="http://img/1.png", ct="image/png", name="1.png"):
        self.url = url
        self.content_type = ct
        self.filename = name


class Msg:
    def __init__(self, mid="m1", content="", attachments=None, openid="U1"):
        self.id = mid
        self.content = content
        self.attachments = attachments or []
        self.author = types.SimpleNamespace(user_openid=openid, member_openid=openid)
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}


async def main() -> int:
    print("[1] 最近图片缓存")
    img = Msg("m1", "", [Att()], "U1")
    bot.remember_recent_image("c2c", "U1", "U1", img)
    hit = bot.recall_recent_image("c2c", "U1", "U1")
    check("能取回同一用户的图", hit is not None and hit.url == "http://img/1.png", hit)
    check("别的用户取不到", bot.recall_recent_image("c2c", "U1", "U2") is None)
    check("别的场景取不到", bot.recall_recent_image("group", "U1", "U1") is None)

    print("[2] 添加命令使用最近图片")
    calls = {}

    class FakeStore:
        async def add_from_attachment(self, **kwargs):
            calls.update(kwargs)
            return types.SimpleNamespace(
                keywords=kwargs.get("keywords") or ["疯狂星期四"],
                size=1024,
                duplicate=False,
                image_id=238,
                sha256="fe7a4e0f8b76" + "0" * 52,
                mime="image/png",
                conversion=None,
                newly_linked=False,
            )

        async def keyword_counts(self, keywords, group_openid=None):
            return {k: 1 for k in keywords or []}

    orig_store = bot.store
    bot.store = FakeStore()
    try:
        cmd = Msg("m2", "添加疯狂星期四", [], "U1")
        await bot.do_add_image(cmd, None, "c2c", "U1", "添加疯狂星期四")
        check("入库用了最近图片", calls.get("url") == "http://img/1.png", calls)
        check("关键词正确", calls.get("keywords") == ["疯狂星期四"], calls)
        check("有成功回复", any("入库成功" in str(r) for r in cmd.replies), cmd.replies)

        calls.clear()
        other = Msg("m3", "添加疯狂星期四", [], "U2")
        await bot.do_add_image(other, None, "c2c", "U1", "添加疯狂星期四")
        check("别的用户不会误用图片", not calls, calls)
        check("无图时保持安静", other.replies == [], other.replies)

        # 过期后不能再用
        bot.RECENT_IMAGE_TTL = -1
        calls.clear()
        expired = Msg("m4", "添加疯狂星期四", [], "U1")
        await bot.do_add_image(expired, None, "c2c", "U1", "添加疯狂星期四")
        check("过期图片不再使用", not calls, calls)
    finally:
        bot.store = orig_store

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
