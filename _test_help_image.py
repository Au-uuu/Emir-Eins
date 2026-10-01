"""帮助图片：渲染、缓存与降级路线。

不碰网络：`_reply_image_bytes` 被替换成假的，只观察调用顺序。
"""
import asyncio
import io
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image  # noqa: E402

import bot  # noqa: E402
import help_image  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    def __init__(self):
        self.replies: list[dict] = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}

    @property
    def text(self) -> str:
        return "\n".join(r.get("content") or "" for r in self.replies)


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="qqbot_help_")

    # ---------------- 1. 没配背景图 ----------------
    print("\n[1] 没配背景图 → 退回纯文本")
    os.environ["QQ_BOT_HELP_BG"] = os.path.join(tmp, "nope.jpg")
    help_image._cache.clear()
    check("render 返回 None", help_image.render("随便一段文字") is None)

    # ---------------- 2. 配了背景图 ----------------
    print("\n[2] 配了背景图 → 输出 JPEG")
    bg = os.path.join(tmp, "bg.jpg")
    Image.new("RGB", (800, 1000), (120, 200, 180)).save(bg, format="JPEG", quality=90)
    os.environ["QQ_BOT_HELP_BG"] = bg
    help_image._cache.clear()

    data = help_image.render("第一行文字\n第二行文字")
    check("拿到字节", isinstance(data, bytes) and len(data) > 1000,
          f"{len(data) if data else 0} bytes")
    check("是 JPEG（FF D8 开头）", bool(data) and data[:2] == b"\xff\xd8",
          data[:2].hex() if data else "")
    with Image.open(io.BytesIO(data)) as im:
        check("宽度等于配置（默认 1280）", im.width == 1280, str(im.size))
    check("第二次命中缓存（同一对象）",
          help_image.render("第一行文字\n第二行文字") is data)

    # ---------------- 3. 降级路线 ----------------
    print("\n[3] 降级路线：图片+按钮 → 图片 → 文本")
    calls: list = []

    async def image_with_keyboard_rejected(message, api, scope, scene_id, data,
                                           name, keyboard=None):
        calls.append(keyboard)
        return keyboard is None  # 带按钮必失败，不带按钮成功

    bot._reply_image_bytes = image_with_keyboard_rejected
    bot._image_keyboard_ok = None

    m = FakeMessage()
    await bot.send_help(m, None, "group", "G1")
    check("第一次尝试带了按钮", len(calls) == 2 and calls[0] is not None, str(len(calls)))
    check("被拒后不带按钮重发", len(calls) == 2 and calls[1] is None, str(calls))
    check("记住了「图片带不了按钮」", bot._image_keyboard_ok is False)
    check("没有退回文本", m.replies == [], str(m.replies))

    calls.clear()
    m2 = FakeMessage()
    await bot.send_help(m2, None, "group", "G1")
    check("下次直接不带按钮，不再浪费一次上传",
          len(calls) == 1 and calls[0] is None, str(calls))

    print("\n[4] 图片整条路走不通 → 退回文本 + 按钮")

    async def always_fail(*args, **kwargs):
        return False

    bot._reply_image_bytes = always_fail
    m3 = FakeMessage()
    await bot.send_help(m3, None, "group", "G1")
    check("回了文本", "我是群助手" in m3.text, m3.text[:40])
    check("文本带按钮", bool(m3.replies and m3.replies[0].get("keyboard")))

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
