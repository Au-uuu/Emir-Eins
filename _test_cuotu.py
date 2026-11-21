# -*- coding: utf-8 -*-
"""搓图：模板解析、渲染、指令接线。"""

import asyncio
import io
import os
import sys
import tempfile
import types

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["QQ_BOT_GROUP_CONTEXT"] = "0"
os.environ["QQ_BOT_MEMORY"] = "0"
os.environ["QQ_BOT_USAGE"] = "0"
os.environ["QQ_BOT_RANDOM_REPLY"] = "0"
os.environ["QQ_BOT_WEBSEARCH"] = "0"
os.environ["QQ_BOT_CUOTU_COOLDOWN"] = "0"
os.environ["QQ_BOT_CHAT_DB"] = os.path.join(tempfile.mkdtemp(), "chat.db")
os.environ["QQ_BOT_KB_DIR"] = os.path.join(tempfile.mkdtemp(), "kb")
os.environ["QQ_BOT_TEXT_KEY"] = "test-key"

import bot  # noqa: E402
import cuotu  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra="") -> None:
    global failures
    suffix = f" -> {extra}" if extra and not ok else ""
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{suffix}")
    if not ok:
        failures += 1


class FakeMessage:
    def __init__(self, text: str):
        self.id = "m1"
        self.content = text
        self.group_openid = "G1"
        self.author = types.SimpleNamespace(user_openid="U1", member_openid="U1")
        self.replies: list[str] = []

    async def reply(self, content=None, **kwargs):
        if content is not None:
            self.replies.append(content)
        return None


async def main() -> int:
    print("[1] 参数拆分与别名")
    name, body = cuotu.split_args("睦子米 今天 不想 上班")
    check("表情名", name == "睦子米", name)
    check("文字保留空格", body == "今天 不想 上班", body)
    check("空参数", cuotu.split_args("") == ("", ""))
    for alias in (
        "睦子米说", "睦子米", "若叶睦", "睦头人", "小黄瓜", "mutsumi", "MuTsUmI",
        "大肥鱼说", "大肥鱼", "DeepSeek说", "deepseek说", "鲸鱼娘说",
    ):
        expected = "deepseek" if ("肥鱼" in alias or "deepseek" in alias.casefold() or "鲸鱼" in alias) else "mutsumi"
        check(f"别名 {alias}", cuotu.normalize_name(alias) == expected)
    check("未知表情", cuotu.normalize_name("不存在") is None)
    check("睦子米字数上限", cuotu.max_text("睦子米说") == 24)
    check("大肥鱼字数上限", cuotu.max_text("大肥鱼说") == 40)

    print("[2] 四张底图都能出图")
    sizes = {1: (512, 512), 2: (384, 384), 3: (500, 500), 4: (500, 500)}
    for number, size in sizes.items():
        data = cuotu.render("睦子米说", "测试文字", number=number)
        im = Image.open(io.BytesIO(data))
        check(f"第 {number} 张", im.format == "JPEG" and im.size == size, im.size)
        check(f"第 {number} 张非空", len(data) > 1000, len(data))

    print("[2b] 大肥鱼说四张底图")
    for number in range(1, 5):
        data = cuotu.render("大肥鱼说", "正在思考中……", number=number)
        im = Image.open(io.BytesIO(data))
        check(f"大肥鱼第 {number} 张",
              im.format == "JPEG" and im.size == (1254, 1254), im.size)
        check(f"大肥鱼第 {number} 张非空", len(data) > 1000, len(data))

    print("[3] 错误输入")
    try:
        cuotu.render("睦子米", "太" * (cuotu.MAX_TEXT + 1))
        check("超长拒绝", False)
    except cuotu.TextTooLong:
        check("超长拒绝", True)
    try:
        cuotu.render("不存在", "测试")
        check("未知模板拒绝", False)
    except cuotu.UnknownTemplate:
        check("未知模板拒绝", True)
    check("列表含睦子米", "睦子米说" in cuotu.list_text())
    check("列表含大肥鱼", "大肥鱼说" in cuotu.list_text())

    print("[4] 指令接线")
    captured = {}

    async def fake_send(message, api, scope, scene_id, data, filename):
        captured["data"] = data
        captured["filename"] = filename
        return True

    bot._reply_image_bytes = fake_send

    msg = FakeMessage("/搓图 睦子米说 测试")
    await bot.do_cuotu(msg, None, "group", "G1", msg.content)
    check("出图", bool(captured.get("data")), captured.get("filename"))
    check("出图不发文字", not msg.replies, msg.replies)

    msg = FakeMessage("/搓图 大肥鱼说 测试")
    await bot.do_cuotu(msg, None, "group", "G1", msg.content)
    check("大肥鱼出图", bool(captured.get("data")), captured.get("filename"))

    msg = FakeMessage("/搓图 不存在 测试")
    await bot.do_cuotu(msg, None, "group", "G1", msg.content)
    check("未知表情提示", bool(msg.replies) and "没有" in msg.replies[-1], msg.replies)

    msg = FakeMessage("/搓图 睦子米")
    await bot.do_cuotu(msg, None, "group", "G1", msg.content)
    check("缺文字提示", bool(msg.replies) and "用法" in msg.replies[-1], msg.replies)

    msg = FakeMessage("/搓图 列表")
    await bot.do_cuotu(msg, None, "group", "G1", msg.content)
    check("列表提示", bool(msg.replies) and "当前支持" in msg.replies[-1], msg.replies)

    msg = FakeMessage("/搓图 睦子米 " + "太" * (cuotu.MAX_TEXT + 1))
    await bot.do_cuotu(msg, None, "group", "G1", msg.content)
    check("超长提示", bool(msg.replies) and "字太多" in msg.replies[-1], msg.replies)

    real_hit = bot.sensitive.filter.hit
    bot.sensitive.filter.hit = lambda t: "测试敏感词" if "敏感词" in t else None
    try:
        msg = FakeMessage("/搓图 睦子米 敏感词")
        await bot.do_cuotu(msg, None, "group", "G1", msg.content)
        check("敏感词拒绝", bool(msg.replies) and "不适合" in msg.replies[-1], msg.replies)
    finally:
        bot.sensitive.filter.hit = real_hit

    check("指令正则", bool(bot.CUOTU_RE.match("/搓图 睦子米 你好")))
    check("帮助已接线", "搓图" in bot.HELP_TEXT and "搓图" in bot.HELP_TOPICS)

    msg = FakeMessage("/搓图 睦子米 测试")
    handled = await bot.handle_command(msg, None, "group", "G1", msg.content)
    check("handle_command 消费指令", handled is True)
    check("handle_command 出图", bool(captured.get("data")))

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
