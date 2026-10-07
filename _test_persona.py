"""人格聊天测试：敏感词过滤、会话记忆隔离、回复流程的静默策略。

不需要真的调模型 —— 用假的 generate() 替换掉 persona.generate。
"""
import asyncio
import base64
import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot  # noqa: E402
import persona  # noqa: E402
import sensitive  # noqa: E402
from chat_store import ChatStore  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    _seq = 0

    def __init__(self, content: str = ""):
        FakeMessage._seq += 1
        self.content = content
        self.id = f"p{FakeMessage._seq}"
        self.group_openid = "G_TEST"
        self.replies: list[dict] = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}

    @property
    def text(self) -> str:
        return "\n".join(r.get("content") or "" for r in self.replies)


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="qqbot_persona_")

    # ---------------- 1. 敏感词过滤 ----------------
    print("\n[1] 敏感词过滤")
    words_path = os.path.join(tmp, "words.txt")
    with open(words_path, "w", encoding="utf-8") as fh:
        fh.write("# 注释行\n测试敏感词\n另一个词\n单\n")

    filt = sensitive.WordFilter(words_path)
    check("词表加载（单字词被丢弃）", filt.size == 2, f"size={filt.size}")
    check("直接命中", filt.hit("这里出现测试敏感词了") == "测试敏感词")
    check("未命中返回 None", filt.hit("今天天气不错") is None)
    check("空格绕过拦得住", filt.hit("测 试 敏 感 词") == "测试敏感词")
    check("符号绕过拦得住", filt.hit("测*试·敏-感_词") == "测试敏感词")
    check("全角绕过拦得住", filt.hit("测　　试－敏感词") == "测试敏感词")
    check("单字词不会误杀", filt.hit("单选一个") is None)

    missing = sensitive.WordFilter(os.path.join(tmp, "nope.txt"))
    check("词表缺失时自动禁用", missing.enabled is False)
    check("禁用时空转", missing.hit("测试敏感词") is None)

    # ---------------- 2. 会话记忆 ----------------
    print("\n[2] 会话记忆：按会话隔离 + 上限裁剪")
    store = ChatStore(db_path=os.path.join(tmp, "chat.db"), max_messages=4)
    await store.append("group:A", "你好", "【肯定】你好")
    await store.append("group:B", "另一个群的消息", "【回答】在")
    hb = await store.history("group:B")
    check("群与群不串台", len(hb) == 2 and hb[0]["content"] == "另一个群的消息", str(hb))

    for i in range(10):
        await store.append("group:A", f"m{i}", f"r{i}")
    ha = await store.history("group:A")
    check("超出上限被裁剪", len(ha) == 4, f"len={len(ha)}")
    check("保留的是最近的", ha[-1]["content"] == "r9", ha[-1]["content"])

    await store.append("group:C", "只有用户这句", None)
    hc = await store.history("group:C")
    check("reply 为空时只记用户那句", len(hc) == 1 and hc[0]["role"] == "user", str(hc))

    check("默认上限 100 条", ChatStore(db_path=os.path.join(tmp, "d.db")).max_messages == 100)

    # ---------------- 2b. 记忆过期（20 分钟） ----------------
    print("\n[2b] 记忆过期：超过存活时间的旧消息不再喂给模型")
    import sqlite3
    import time as _time

    ttl_path = os.path.join(tmp, "ttl.db")
    ttl_store = ChatStore(db_path=ttl_path, max_messages=100, memory_minutes=20)
    await ttl_store.append("c2c:u1", "刚说的话", "【回答】好")

    conn = sqlite3.connect(ttl_path)
    conn.execute(
        "INSERT INTO messages(scope, role, content, ts) VALUES (?, ?, ?, ?)",
        ("c2c:u1", "user", "21分钟前的旧话", _time.time() - 21 * 60),
    )
    conn.commit()
    conn.close()

    texts = [x["content"] for x in await ttl_store.history("c2c:u1")]
    check(
        "过期消息被排除在上下文外",
        "21分钟前的旧话" not in texts and "刚说的话" in texts,
        str(texts),
    )

    await ttl_store.append("c2c:u1", "触发一次写入", "【回答】好")
    conn = sqlite3.connect(ttl_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE scope='c2c:u1' AND content='21分钟前的旧话'"
    ).fetchone()[0]
    conn.close()
    check("写入时物理清理过期行", n == 0, f"残留 {n} 行")

    # ---------------- 3. 回复流程 ----------------
    print("\n[3] 回复流程的静默策略")
    bot.chat = ChatStore(db_path=os.path.join(tmp, "chat2.db"), max_messages=100)
    bot.sensitive.filter = filt
    bot.chat_cooldown.seconds = 0
    persona.available = lambda: True

    async def ok_generate(history, text):
        return f"【回答】收到「{text}」上下文{len(history)}条"

    async def none_generate(history, text):
        return None

    async def bad_generate(history, text):
        return "这条回复里有测试敏感词"

    persona.generate = ok_generate
    m = FakeMessage("今天天气不错")
    await bot.persona_reply(m, "group:G1", "今天天气不错")
    check("正常回复", "收到" in m.text, m.text)
    check("上下文已写入", len(await bot.chat.history("group:G1")) == 2)

    m = FakeMessage("这是测试敏感词")
    await bot.persona_reply(m, "group:G1", "这是测试敏感词")
    check("用户命中敏感词 → 静默", m.replies == [], str(m.replies))
    check("且不写进上下文", len(await bot.chat.history("group:G1")) == 2)

    persona.generate = none_generate
    m = FakeMessage("模型会失败")
    await bot.persona_reply(m, "group:G1", "模型会失败")
    check("模型返回空 → 静默", m.replies == [], str(m.replies))

    persona.generate = bad_generate
    m = FakeMessage("正常提问")
    await bot.persona_reply(m, "group:G2", "正常提问")
    check("模型回复命中敏感词 → 丢弃不发送", m.replies == [], str(m.replies))
    h = await bot.chat.history("group:G2")
    check("只记用户那句（回复不回滚）", len(h) == 1 and h[0]["role"] == "user", str(h))

    persona.generate = ok_generate
    bot.chat_cooldown.seconds = 60
    bot.chat_cooldown._last.clear()
    m1 = FakeMessage("第一次")
    m2 = FakeMessage("第二次")
    await bot.persona_reply(m1, "group:G3", "第一次")
    await bot.persona_reply(m2, "group:G3", "第二次")
    check(
        "限流内只回一次",
        len(m1.replies) == 1 and m2.replies == [],
        f"{len(m1.replies)}/{len(m2.replies)}",
    )
    m3 = FakeMessage("别的会话")
    await bot.persona_reply(m3, "group:G4", "别的会话")
    check("限流按会话独立", len(m3.replies) == 1, str(m3.replies))

    persona.available = lambda: False
    m = FakeMessage("没配 Key")
    await bot.persona_reply(m, "group:G5", "没配 Key")
    check("未配置 API Key → 完全不回复", m.replies == [], str(m.replies))

    # ---------------- 4. 全量模式：靠 mentions 判定 @ ----------------
    print("\n[4] 全量模式：靠 payload 的 mentions[].is_you 判定「被 @」")
    import raw_events

    class GroupFakeMessage:
        _seq = 0

        def __init__(self, content: str, mentions=None):
            GroupFakeMessage._seq += 1
            self.content = content
            self.attachments: list = []
            self.id = f"g{GroupFakeMessage._seq}"
            self.group_openid = "G_ALL"
            self.author = types.SimpleNamespace(member_openid="user_x")
            self.replies: list[dict] = []
            if mentions is not None:
                raw_events._remember(self.id, {"d": {"mentions": mentions}})

        async def reply(self, **kwargs):
            self.replies.append(kwargs)
            return {"id": "sent"}

        @property
        def text(self) -> str:
            return "\n".join(r.get("content") or "" for r in self.replies)

    class GroupFakeApi:
        async def post_group_message(self, **kwargs):
            return {"id": "m"}

    group_client = types.SimpleNamespace(api=GroupFakeApi())

    async def group_all(m):
        await bot.MyClient.on_group_message_create(group_client, m)

    persona.available = lambda: True
    persona.generate = ok_generate
    bot.chat_cooldown.seconds = 0

    m = GroupFakeMessage(
        "<@BOTOPENID> 111",
        mentions=[{"is_you": True, "bot": True, "username": "依蜜尔爱因-测试中"}],
    )
    await group_all(m)
    check("明确 @ 机器人 → 人格回复", len(m.replies) == 1, str(m.replies))

    m2 = GroupFakeMessage(
        "<@SOMEONE> 你好啊",
        mentions=[{"is_you": False, "bot": False, "username": "别人"}],
    )
    await group_all(m2)
    check("回复别人（mentions 无 is_you）→ 不回复", m2.replies == [], str(m2.replies))

    m3 = GroupFakeMessage("222")
    await group_all(m3)
    check("普通群消息（无 mentions）→ 不回复", m3.replies == [], str(m3.replies))

    # ---------------- 5. 看图链路 ----------------
    print("\n[5] 看图：带图消息走视觉模型，图不可用自动退回旧行为")

    # content 构造：纯文本 vs 多模态
    check("纯文本 content 保持字符串", persona._user_content("你好", []) == "你好")
    parts = persona._user_content("看看", [("image/png", b"png")])
    check("带图 content 是数组", isinstance(parts, list) and len(parts) == 2, str(type(parts)))
    check("图片在文字前", parts[0]["type"] == "image_url" and parts[1]["type"] == "text")
    check(
        "data URI 形态正确",
        parts[0]["image_url"]["url"].startswith("data:image/png;base64,"),
    )

    # 视觉模型配置
    check("视觉模型默认 qwen3-vl-flash", persona.vl_model() == "qwen3-vl-flash")
    persona.available = lambda: True
    old_vl = os.environ.get("QQ_BOT_QWEN_VL_MODEL")
    os.environ["QQ_BOT_QWEN_VL_MODEL"] = "qwen3-vl-flash"
    check("默认开启看图", persona.vision_enabled() is True)
    os.environ["QQ_BOT_QWEN_VL_MODEL"] = "off"
    check("设为 off 可关闭看图", persona.vision_enabled() is False)
    if old_vl is None:
        os.environ.pop("QQ_BOT_QWEN_VL_MODEL", None)
    else:
        os.environ["QQ_BOT_QWEN_VL_MODEL"] = old_vl

    class ImgMessage(FakeMessage):
        def __init__(self, content: str = "", atts=None):
            super().__init__(content)
            self.attachments = list(atts or [])

    def make_att(ct="image/png", url="http://img/a.png"):
        return types.SimpleNamespace(content_type=ct, filename="a.png", url=url)

    PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes"
    GIF_BYTES = base64.b64decode(
        "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    )

    async def png_download(url):
        return PNG_BYTES

    seen = {}

    async def fake_vision(history, text, images):
        seen["history"], seen["text"], seen["images"] = history, text, images
        return "【看图】我看到了"

    async def fail_generate(history, text):
        raise AssertionError("带图消息不该走纯文本模型")

    persona.available = lambda: True
    persona.vision_enabled = lambda: True
    persona.generate = fail_generate
    persona.generate_vision = fake_vision
    bot.store.download = png_download

    m = ImgMessage("这张图是什么", [make_att()])
    await bot.persona_reply(m, "group:V1", "这张图是什么")
    check("带图文字消息 → 视觉模型回复", "看到" in m.text, m.text)
    check(
        "图片传给了模型",
        len(seen.get("images", [])) == 1 and seen["images"][0][0] == "image/png",
    )
    check("文字一并传给模型", seen.get("text") == "这张图是什么")
    check("上下文照常记录", len(await bot.chat.history("group:V1")) == 2)

    m = ImgMessage("", [make_att()])
    await bot.persona_reply(m, "c2c:V2", bot.IMAGE_ONLY_PLACEHOLDER)
    check("只发图片也能收到看图回复", "看到" in m.text, m.text)
    h = await bot.chat.history("c2c:V2")
    check(
        "占位文本照旧记进上下文",
        len(h) == 2 and h[0]["content"] == bot.IMAGE_ONLY_PLACEHOLDER,
        str(h),
    )

    async def none_vision(history, text, images):
        return None

    persona.generate_vision = none_vision
    m = ImgMessage("", [make_att()])
    await bot.persona_reply(m, "c2c:V3", bot.IMAGE_ONLY_PLACEHOLDER)
    check("视觉模型失败 → 不回复", m.replies == [])
    h = await bot.chat.history("c2c:V3")
    check(
        "视觉模型失败 → 占位仍记进上下文",
        len(h) == 1 and h[0]["content"] == bot.IMAGE_ONLY_PLACEHOLDER,
    )

    persona.vision_enabled = lambda: False
    m = ImgMessage("", [make_att()])
    await bot.persona_reply(m, "c2c:V4", bot.IMAGE_ONLY_PLACEHOLDER)
    check("未开看图 → 不回复", m.replies == [])
    h = await bot.chat.history("c2c:V4")
    check("未开看图 → 记占位（旧行为）", len(h) == 1 and h[0]["content"] == bot.IMAGE_ONLY_PLACEHOLDER)
    persona.vision_enabled = lambda: True

    async def bad_download(url):
        raise OSError("下载失败")

    bot.store.download = bad_download
    m = ImgMessage("", [make_att()])
    await bot.persona_reply(m, "c2c:V5", bot.IMAGE_ONLY_PLACEHOLDER)
    check("图片下载失败 → 不回复", m.replies == [])
    h = await bot.chat.history("c2c:V5")
    check("图片下载失败 → 记占位", len(h) == 1 and h[0]["content"] == bot.IMAGE_ONLY_PLACEHOLDER)
    bot.store.download = png_download

    imgs = await bot.collect_vision_images(
        ImgMessage("", [make_att(ct="image/gif")])
    )
    check(
        "假 GIF（魔数是 PNG）不受抽帧影响",
        len(imgs) == 1 and imgs[0][0] == "image/png" and imgs[0][1][:4] == b"\x89PNG",
    )

    # 真·动图：PIL 生成 4 帧彩色 GIF（红/绿/蓝/黄），验证抽帧
    import io as _io

    from PIL import Image as _Img

    _buf = _io.BytesIO()
    _frames = [
        _Img.new("RGB", (32, 32), c)
        for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
    ]
    _frames[0].save(
        _buf, format="GIF", save_all=True, append_images=_frames[1:], duration=100
    )

    async def gif_download(url):
        return _buf.getvalue()

    bot.store.download = gif_download
    imgs = await bot.collect_vision_images(
        ImgMessage("", [make_att(ct="image/gif")])
    )
    check(
        "单发动图抽 3 帧（PNG）",
        len(imgs) == 3 and all(m == "image/png" and d[:4] == b"\x89PNG" for m, d in imgs),
        f"n={len(imgs)}",
    )
    check("抽的是首/中/尾不同帧", len({d for _, d in imgs}) == 3)

    imgs = await bot.collect_vision_images(
        ImgMessage("", [make_att(ct="image/gif"), make_att(url="http://img/b.png")])
    )
    check(
        "动图混发时只看首帧（共 2 张）",
        len(imgs) == 2 and all(m == "image/png" for m, _ in imgs),
        f"n={len(imgs)}",
    )
    bot.store.download = png_download

    imgs = await bot.collect_vision_images(
        ImgMessage("", [make_att(url=f"http://img/{i}.png") for i in range(5)])
    )
    check("一次最多喂 3 张", len(imgs) == 3, f"n={len(imgs)}")

    persona.generate_vision = fake_vision
    m = GroupFakeMessage(
        "",
        mentions=[{"is_you": True, "bot": True, "username": "依蜜尔爱因-测试中"}],
    )
    m.attachments = [make_att()]
    await group_all(m)
    check("群@只发图 → 看图回复而不是 /help", len(m.replies) == 1 and "看到" in m.text, str(m.replies))

    persona.vision_enabled = lambda: False
    m = GroupFakeMessage(
        "",
        mentions=[{"is_you": True, "bot": True, "username": "依蜜尔爱因-测试中"}],
    )
    m.attachments = [make_att()]
    await group_all(m)
    check(
        "群@只发图 + 未开看图 → 维持 /help",
        len(m.replies) == 1 and "群助手" in m.text,
        str(m.replies)[:60],
    )
    persona.vision_enabled = lambda: True

    # ---------------- 6. 引用消息：引用文本进模型 + 引用卡片里的图 ----------------
    print("\n[6] 引用消息：引用文本与合并转发卡片")

    QUOTE_BLOCK = (
        "=== 消息 1 ===\n[消息内容] [群聊的聊天记录]\n[消息类型] 引用消息\n"
        "--- 第1条 ---\n    [发送者] s_hamster\n"
        "    [附件1] 类型:图片 文件名:a.jpg URL:https://img.example/a.jpg"
    )

    m = FakeMessage("这句话啥意思")
    raw_events._remember(m.id, {"d": {"msg_elements": [{"content": QUOTE_BLOCK}]}})
    q = bot.quoted_content(m)
    check("引用文本被抽出", "引用消息" in q and "s_hamster" in q, q[:40])
    check("引用里的 URL 被抹掉", "img.example" not in q)

    m = FakeMessage("没有引用")
    check("无引用返回空串", bot.quoted_content(m) == "")

    persona.available = lambda: True
    persona.vision_enabled = lambda: True
    cap = {}

    async def vision_cap(history, text, images):
        cap["text"], cap["imgs"] = text, images
        return "【看图】好的"

    persona.generate = fail_generate
    persona.generate_vision = vision_cap

    async def url_png_download(url):
        return PNG_BYTES

    bot.store.download = url_png_download
    m = FakeMessage("这是什么")
    raw_events._remember(m.id, {"d": {"msg_elements": [{"content": QUOTE_BLOCK}]}})
    await bot.persona_reply(m, "group:QT1", "这是什么")
    check(
        "引用文本拼进模型输入",
        "引用了下面这条消息" in cap.get("text", "") and "s_hamster" in cap["text"],
    )
    check(
        "引用块里的图片被取到",
        len(cap.get("imgs", [])) == 1 and cap["imgs"][0][0] == "image/png",
    )

    persona.vision_enabled = lambda: False

    async def text_cap(history, text):
        cap["text2"] = text
        return "【回答】好的"

    persona.generate = text_cap

    async def boom_download(url):
        raise AssertionError("未开看图不该下载引用图")

    bot.store.download = boom_download
    m = FakeMessage("这是什么")
    raw_events._remember(m.id, {"d": {"msg_elements": [{"content": QUOTE_BLOCK}]}})
    await bot.persona_reply(m, "group:QT2", "这是什么")
    check("未开看图时引用文本仍进模型", "引用了下面这条消息" in cap.get("text2", ""))
    persona.vision_enabled = lambda: True
    bot.store.download = png_download

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
