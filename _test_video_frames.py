# -*- coding: utf-8 -*-
"""视频抽帧：计划、开关、接入人格链路。"""

import asyncio
import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["QQ_BOT_VIDEO_MODE"] = "frames"
os.environ["QQ_BOT_VIDEO_FRAME_INTERVAL"] = "15"
os.environ["QQ_BOT_VIDEO_MIN_FRAMES"] = "9"
os.environ["QQ_BOT_VIDEO_MAX_FRAMES"] = "30"
os.environ["QQ_BOT_GROUP_CONTEXT"] = "0"
os.environ["QQ_BOT_GROUP_CONTEXT_DB"] = os.path.join(tempfile.mkdtemp(), "gc.db")
os.environ["QQ_BOT_MEMORY"] = "0"
os.environ["QQ_BOT_MEMORY_DB"] = os.path.join(tempfile.mkdtemp(), "mem.db")
os.environ["QQ_BOT_CHAT_DELAY_MIN"] = "0"
os.environ["QQ_BOT_CHAT_DELAY_MAX"] = "0"
os.environ["QQ_BOT_CHAT_SPLIT"] = "0"
os.environ["QQ_BOT_WEBSEARCH"] = "0"
os.environ["QQ_BOT_KB_DIR"] = os.path.join(tempfile.mkdtemp(), "kb")

import bot  # noqa: E402
import persona  # noqa: E402
import video_frames  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    _seq = 0

    def __init__(self, content=""):
        FakeMessage._seq += 1
        self.id = f"vv{FakeMessage._seq}"
        self.content = content
        self.group_openid = "G"
        self.author = types.SimpleNamespace(member_openid="u", user_openid="u")
        self.attachments = [
            types.SimpleNamespace(
                content_type="video/mp4", filename="v.mp4", url="http://v/x.mp4"
            )
        ]
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}


async def main() -> int:
    print("[1] 抽帧计划")
    n, step = video_frame_plan = video_frames.frame_plan(10)
    check("10 秒视频至少 9 帧", n == 9, (n, step))
    check("短视频间隔 <1.5 秒", step < 1.5, step)
    n2, step2 = video_frames.frame_plan(150)
    check("150 秒约 10 帧", n2 == 10, (n2, step2))
    check("长视频约 15 秒一帧", abs(step2 - 15) < 3, step2)
    n3, step3 = video_frames.frame_plan(10000)
    check("超长视频封顶 30 帧", n3 == 30, n3)

    print("[2] 接入 persona_reply")
    seen = {}

    async def fake_describe(images, video_url=None, hint_text="", session=""):
        seen["images"] = images
        seen["video"] = video_url
        return "视频里有人在走路。"

    async def fake_generate(history, text, session=""):
        return "看到了。"

    orig_frames = video_frames.frames_from_url
    orig_describe = persona.describe
    orig_generate = persona.generate
    orig_available = persona.available
    orig_vision = persona.vision_enabled
    orig_describe_mode = persona.describe_mode
    video_frames.frames_from_url = lambda url: [("image/jpeg", b"frame-1"), ("image/jpeg", b"frame-2")]
    persona.describe = fake_describe
    persona.generate = fake_generate
    persona.available = lambda: True
    persona.vision_enabled = lambda: True
    persona.describe_mode = lambda: True
    try:
        m = FakeMessage("看看这个视频")
        await bot.chat.clear("group:V1")
        await bot.persona_reply(m, "group:V1", "看看这个视频")
        check("抽帧传给了视觉模型", len(seen.get("images") or []) == 2, seen)
        check("video_url 已清空（DS 不吃）", not seen.get("video"), seen.get("video"))
        check("正常回复", bool(m.replies), m.replies)
    finally:
        video_frames.frames_from_url = orig_frames
        persona.describe = orig_describe
        persona.generate = orig_generate
        persona.available = orig_available
        persona.vision_enabled = orig_vision
        persona.describe_mode = orig_describe_mode

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
