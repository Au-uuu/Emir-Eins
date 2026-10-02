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
    # 下面几节会把 _reply_image_bytes 换成假的，这里先留一份真的，后面换回来
    real_reply_image_bytes = bot._reply_image_bytes

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
    print("\n[3] 图片发得出去 → 就用图片，不再回文本")
    calls: list = []

    async def ok_send(message, api, scope, scene_id, data, name):
        calls.append(name)
        return True

    bot._reply_image_bytes = ok_send
    m = FakeMessage()
    await bot.send_help(m, None, "group", "G1")
    check("走的是图片", calls == ["help.jpg"], str(calls))
    check("没有额外回文本", m.replies == [], str(m.replies))

    print("\n[4] 图片发不出去 → 退回纯文本（且不带按钮）")

    async def always_fail(*args, **kwargs):
        return False

    bot._reply_image_bytes = always_fail
    m3 = FakeMessage()
    await bot.send_help(m3, None, "group", "G1")
    check("回了文本", "我是群助手" in m3.text, m3.text[:40])
    check("不再有 keyboard 字段", "keyboard" not in m3.replies[0],
          str(list(m3.replies[0].keys())))

    print("\n[5] file_info 缓存：同一会话同一文件只上传一次")
    bot._reply_image_bytes = real_reply_image_bytes  # 换回真的实现
    bot._file_info_cache.clear()
    uploads: list = []

    async def fake_uncached(up, scope, scene_id, path, name):
        uploads.append((scope, scene_id, name))
        return f"fileinfo#{len(uploads)}"

    bot._upload_local_image_uncached = fake_uncached

    class DummyApi:
        pass

    class SendMsg:
        def __init__(self):
            self.replies: list = []

        async def reply(self, **kwargs):
            self.replies.append(kwargs)
            return {"id": "x"}

    payload = b"same-bytes-for-cache-test"
    check("第一次发送成功",
          await bot._reply_image_bytes(SendMsg(), DummyApi(), "group", "G1", payload, "a.jpg"))
    check("第二次发送成功（走缓存）",
          await bot._reply_image_bytes(SendMsg(), DummyApi(), "group", "G1", payload, "a.jpg"))
    check("只真正上传了一次", len(uploads) == 1, str(uploads))

    await bot._reply_image_bytes(SendMsg(), DummyApi(), "group", "G2", payload, "a.jpg")
    check("换个群要重新上传（上传接口按会话隔离）", len(uploads) == 2, str(uploads))

    await bot._reply_image_bytes(SendMsg(), DummyApi(), "c2c", "U1", b"other", "b.jpg")
    check("内容不同要重新上传", len(uploads) == 3, str(uploads))

    print("\n[6] file_info 过期导致发送失败 → 忽略缓存重传一次自愈")
    bot._file_info_cache.clear()
    uploads.clear()

    class FlakyMsg:
        def __init__(self):
            self.n = 0

        async def reply(self, **kwargs):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("file_info 已过期")
            return {"id": "x"}

    ok = await bot._reply_image_bytes(
        FlakyMsg(), DummyApi(), "group", "G9", b"flaky-payload", "c.jpg"
    )
    check("最终发送成功", ok is True)
    check("失败后强制重传了一次", len(uploads) == 2, str(uploads))

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
