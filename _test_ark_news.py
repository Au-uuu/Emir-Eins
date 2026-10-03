"""
游戏公告推送测试：数据源解析、关键词过滤、开关指令、轮询推送、保险丝。

覆盖需求：
  - 终末地官网 SSR HTML 能离线解析出公告列表（含引号/括号等复杂字符）
  - 只有标题命中关键词的公告才主动推送；/公告 查 不过滤类型
  - 每个群 × 每个游戏独立开关，默认关
  - 首次初始化（该游戏 seen 表为空）只把既有公告标记为已见、不推送；之后只推新公告。
    判定依据是**持久化的 seen 表**而不是「本进程第一轮」，所以重启不会吞掉公告
  - 每群每日推送上限保险丝生效
  - /公告 的开关 / 状态 / 查 / 错误参数 / 私聊限制
"""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ark_news  # noqa: E402
import bot  # noqa: E402
from push_store import PushStore  # noqa: E402

TEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_ark_news")
failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


class FakeMessage:
    def __init__(self, msg_id="m1", group_openid="G_TEST"):
        self.content = ""
        self.attachments = []
        self.id = msg_id
        self.group_openid = group_openid
        self.author = types.SimpleNamespace(member_openid="U_TEST", user_openid=None)
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)


class FakeAPI:
    def __init__(self):
        self.sent = []

    async def post_group_message(self, **kwargs):
        self.sent.append(kwargs)


# ---------------------------------------------------------------------------
# 1. 终末地 SSR HTML 解析
# ---------------------------------------------------------------------------
def make_endfield_html(bulletins: list[dict]) -> str:
    # 模拟 Next.js RSC flight 数据块：先拼 flight 片段，再按 JSON 字符串转义塞进 HTML
    flight = '6:["$","$L1a",null,{"value":{"bulletins":' + json.dumps(
        bulletins, ensure_ascii=False
    ) + "}}]"
    return (
        "<html><body><script>self.__next_f.push([1,"
        + json.dumps(flight)
        + "])</script>"
        "<script>self.__next_f.push([1,\"other:chunk\"])</script></body></html>"
    )


def test_endfield_extraction():
    print("[1] 终末地 HTML 解析")
    bulletins = [
        {
            "cid": "5987",
            "tab": "events",
            "sticky": True,
            "title": "「融合！山团团」趣味活动]说明\"带引号\"",
            "author": "",
            "displayTime": 1790751600,
            "cover": "",
            "extraCover": "",
            "brief": "含括号与引号的}简介]文本",
        },
        {"cid": "2653", "tab": "notices", "title": "版本更新维护公告", "displayTime": 1790000000},
        {"cid": "1", "tab": "notices", "title": "", "displayTime": 1},  # 脏数据
    ]
    html = make_endfield_html([b for b in bulletins])
    items = ark_news.extract_endfield_bulletins(html)
    check("提取出 3 条原始记录", len(items) == 3, f"got {len(items)}")

    # 走 NewsItem 转换：脏数据（空标题）应被丢弃
    converted = [x for x in (ark_news._item_from_raw("endfield", r) for r in items) if x]
    check("脏数据被丢弃后剩 2 条", len(converted) == 2, f"got {len(converted)}")
    check(
        "特殊字符无损",
        converted[0].title == "「融合！山团团」趣味活动]说明\"带引号\"",
        converted[0].title,
    )
    check("详情页 URL", converted[0].url == "https://endfield.hypergryph.com/news/5987")

    try:
        ark_news.extract_endfield_bulletins("<html>没有数据</html>")
        check("无数据时抛 ValueError", False)
    except ValueError:
        check("无数据时抛 ValueError", True)


# ---------------------------------------------------------------------------
# 2. 过滤与别名
# ---------------------------------------------------------------------------
def test_filter_and_aliases():
    print("[2] 关键词过滤与游戏别名")
    # 关键词来自环境变量（每次调用都读，无缓存）。**显式钉死**成默认值，
    # 否则这条会跟着服务器 .env 走——在服务器上就跑出过 2 项失败。
    os.environ["QQ_BOT_NEWS_KEYWORDS"] = "更新|维护|停机|版本"
    check("更新公告命中", ark_news.is_update_news("[明日方舟]09月29日16:00闪断更新公告"))
    check("维护公告命中", ark_news.is_update_news("【维护公告】10月10日更新维护"))
    check("活动预告不命中（默认关键词）", not ark_news.is_update_news("[活动预告]矢量突破#3「拟生态」限时活动即将开启"))
    check("制作组通讯不命中", not ark_news.is_update_news("《明日方舟》制作组通讯#69期"))

    # 线上 .env 把「活动预告」也加进了关键词，验证放宽后确实会命中
    os.environ["QQ_BOT_NEWS_KEYWORDS"] = "更新|维护|停机|版本|活动预告"
    check("放宽后活动预告命中", ark_news.is_update_news("[活动预告]矢量突破#3「拟生态」限时活动即将开启"))
    check("放宽后制作组通讯仍不命中", not ark_news.is_update_news("《明日方舟》制作组通讯#69期"))
    os.environ.pop("QQ_BOT_NEWS_KEYWORDS", None)

    check("明日方舟别名", ark_news.resolve_game("明日方舟") == "ak")
    check("舟 别名", ark_news.resolve_game("舟") == "ak")
    check("大小写 AK", ark_news.resolve_game("AK") == "ak")
    check("终末地", ark_news.resolve_game("终末地") == "endfield")
    check("Endfield", ark_news.resolve_game("Endfield") == "endfield")
    check("未知游戏", ark_news.resolve_game("原神") is None)


# ---------------------------------------------------------------------------
# 3. PushStore
# ---------------------------------------------------------------------------
def test_push_store():
    print("[3] PushStore 开关与已见公告")
    db = os.path.join(TEST_DIR, "push.db")
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    os.makedirs(TEST_DIR, exist_ok=True)
    ps = PushStore(db_path=db)

    check("首次开启返回变化", asyncio.run(ps.set_enabled("G1", "ak", True)))
    check("重复开启返回无变化", not asyncio.run(ps.set_enabled("G1", "ak", True)))
    asyncio.run(ps.set_enabled("G1", "endfield", True))

    games = asyncio.run(ps.enabled_games("G1"))
    check("enabled_games 两游戏为开", games == {"ak": True, "endfield": True}, str(games))
    check("未记录的群为空", asyncio.run(ps.enabled_games("G2")) == {})

    check("enabled_groups(ak) 含 G1", "G1" in asyncio.run(ps.enabled_groups("ak")))
    asyncio.run(ps.set_enabled("G1", "ak", False))
    check("关闭后 enabled_groups(ak) 不含 G1", "G1" not in asyncio.run(ps.enabled_groups("ak")))

    check("未见全保留", asyncio.run(ps.filter_unseen("ak", ["a", "b", "a"])) == ["a", "b"])
    asyncio.run(ps.mark_seen("ak", ["a"]))
    check("已见的被筛掉", asyncio.run(ps.filter_unseen("ak", ["a", "b", "c"])) == ["b", "c"])
    asyncio.run(ps.mark_seen("ak", ["a"]))
    check("mark_seen 幂等", asyncio.run(ps.filter_unseen("ak", ["a"])) == [])


# ---------------------------------------------------------------------------
# 4. 轮询推送（mock 数据源）
# ---------------------------------------------------------------------------
AK_OLD = ark_news.NewsItem("ak", "1", "旧活动公告", 1790000000, "1", "")
AK_NEW = ark_news.NewsItem("ak", "2", "[明日方舟]版本更新维护公告", 1790100000, "0", "")
AK_NEW_EVENT = ark_news.NewsItem("ak", "3", "[活动预告]新限时活动", 1790110000, "1", "")
EF_NEW = ark_news.NewsItem("endfield", "9", "云·终末地版本调整公告", 1790200000, "notices", "")


def fake_fetch_factory(items_by_game: dict):
    async def fake_fetch(session, game):
        return items_by_game[game]

    return fake_fetch


def test_poll_once():
    print("[4] 轮询推送")
    # 同样钉死关键词，避免跟着 .env 变
    os.environ["QQ_BOT_NEWS_KEYWORDS"] = "更新|维护|停机|版本"
    db = os.path.join(TEST_DIR, "poll.db")
    os.makedirs(TEST_DIR, exist_ok=True)
    if os.path.exists(db):
        os.remove(db)
    ps = PushStore(db_path=db)
    # 首次初始化的判定依据是「该游戏 seen 表是否为空」（持久化），不是进程第一轮：
    # 这样重启后不会把重启期间的新公告静默标记为已见
    check("初始化前 has_seen 为假", asyncio.run(ps.has_seen("ak")) is False)
    # 首轮：线上只有旧公告
    ark_news.fetch_game = fake_fetch_factory({"ak": [AK_OLD], "endfield": [EF_NEW]})

    # 首轮：只标记已见，不推送
    api = FakeAPI()
    asyncio.run(ark_news.poll_once(None, api, ps))
    check("首次初始化不推送", api.sent == [])
    check("既有公告全部标记已见", asyncio.run(ps.filter_unseen("ak", ["1"])) == [])
    check("初始化后 has_seen 为真", asyncio.run(ps.has_seen("ak")) is True)

    # 群开启 ak（endfield 不开）→ 第二轮冒出更新公告和活动公告，只推前者
    asyncio.run(ps.set_enabled("G1", "ak", True))
    ark_news.fetch_game = fake_fetch_factory(
        {"ak": [AK_OLD, AK_NEW, AK_NEW_EVENT], "endfield": [EF_NEW]}
    )
    api = FakeAPI()
    asyncio.run(ark_news.poll_once(None, api, ps))
    check("只推 1 条", len(api.sent) == 1, str(len(api.sent)))
    if api.sent:
        content = api.sent[0]["content"]
        check("群 openid 正确", api.sent[0]["group_openid"] == "G1")
        check("推的是更新公告", "版本更新维护公告" in content)
        check("活动公告不推", "活动预告" not in content)
        check("带详情链接", "https://ak.hypergryph.com/news/2" in content)

    # 同一条公告不重推
    api2 = FakeAPI()
    asyncio.run(ark_news.poll_once(None, api2, ps))
    check("已见公告不重推", api2.sent == [])

    # endfield 独立开关：G2 只开终末地，G1 只开 ak，各收各的
    asyncio.run(ps.set_enabled("G2", "endfield", True))
    ef_new2 = ark_news.NewsItem("endfield", "10", "版本停机维护公告", 1790300000, "notices", "")
    ark_news.fetch_game = fake_fetch_factory({"ak": [], "endfield": [EF_NEW, ef_new2]})
    api3 = FakeAPI()
    asyncio.run(ark_news.poll_once(None, api3, ps))
    check("终末地推 1 条到 G2", len(api3.sent) == 1 and api3.sent[0]["group_openid"] == "G2")

    # 每日上限保险丝
    ark_news._daily_pushed.clear()
    today = __import__("time").strftime("%Y-%m-%d")
    ark_news._daily_pushed[("G2", today)] = ark_news._daily_limit()
    ef_new3 = ark_news.NewsItem("endfield", "11", "版本更新公告", 1790400000, "notices", "")
    ark_news.fetch_game = fake_fetch_factory({"ak": [], "endfield": [ef_new3]})
    api4 = FakeAPI()
    asyncio.run(ark_news.poll_once(None, api4, ps))
    check("达到每日上限后跳过", api4.sent == [])
    ark_news._daily_pushed.clear()

    # 放宽关键词（线上 .env 的配置）后，活动预告也应该推出来
    os.environ["QQ_BOT_NEWS_KEYWORDS"] = "更新|维护|停机|版本|活动预告"
    event4 = ark_news.NewsItem("ak", "4", "[活动预告]新限时活动", 1790500000, "1", "")
    ark_news.fetch_game = fake_fetch_factory({"ak": [AK_OLD, event4], "endfield": []})
    api5 = FakeAPI()
    asyncio.run(ark_news.poll_once(None, api5, ps))
    check(
        "放宽关键词后活动预告也推给已开的群",
        len(api5.sent) == 1 and "活动预告" in api5.sent[0]["content"],
        str(len(api5.sent)),
    )
    os.environ.pop("QQ_BOT_NEWS_KEYWORDS", None)


# ---------------------------------------------------------------------------
# 5. /公告 指令层
# ---------------------------------------------------------------------------
def _content(msg) -> str:
    return msg.replies[0].get("content", "") if msg.replies else ""


def test_announce_command():
    print("[5] /公告 指令")
    gid = "G_TEST_CMD"

    # 清掉该群在真实 push.db 里的记录（测试后同样清理）
    def cleanup():
        with sqlite3.connect(bot.push_store.db_path) as conn:
            conn.execute("DELETE FROM push_groups WHERE group_openid = ?", (gid,))

    cleanup()
    try:
        msg = FakeMessage(group_openid=gid)
        asyncio.run(bot.handle_command(msg, None, "group", gid, "/公告"))
        body = _content(msg)
        check("状态含两游戏", "明日方舟" in body and "终末地" in body)
        check("默认都是关", body.count("：关") == 2, body.splitlines()[1] if body else "")

        msg = FakeMessage(group_openid=gid)
        asyncio.run(bot.handle_command(msg, None, "group", gid, "/公告 开 明日方舟"))
        check("开启成功", "已开" in _content(msg), _content(msg))

        msg = FakeMessage(group_openid=gid)
        asyncio.run(bot.handle_command(msg, None, "group", gid, "/公告开启明日方舟"))
        check("无空格写法+重复开", "本来就是开" in _content(msg), _content(msg))

        msg = FakeMessage(group_openid=gid)
        asyncio.run(bot.handle_command(msg, None, "group", gid, "/公告"))
        check("状态变开", "：开" in _content(msg))

        msg = FakeMessage(group_openid=gid)
        asyncio.run(bot.handle_command(msg, None, "group", gid, "/公告 开"))
        check("缺游戏名给用法", "用法" in _content(msg))

        msg = FakeMessage(group_openid=gid)
        asyncio.run(bot.handle_command(msg, None, "group", gid, "/公告 开 原神"))
        check("未知游戏报错", "没认出" in _content(msg))

        msg = FakeMessage(group_openid=gid)
        asyncio.run(bot.handle_command(msg, None, "group", gid, "/公告 关 全部"))
        check("全部关闭", _content(msg).count("已关") == 2, _content(msg))

        # 私聊：开关被拒
        msg = FakeMessage()
        asyncio.run(bot.handle_command(msg, None, "c2c", "U_TEST", "/公告 开 明日方舟"))
        check("私聊不能开关", "群聊" in _content(msg))

        # 私聊状态提示
        msg = FakeMessage()
        asyncio.run(bot.handle_command(msg, None, "c2c", "U_TEST", "/公告"))
        check("私聊状态有提示", "只在群聊里生效" in _content(msg))

        # 查询（mock 网络）
        async def fake_latest(game, limit=5):
            return f"LATEST_{game}_TEST"

        original = ark_news.latest_text
        ark_news.latest_text = fake_latest
        try:
            bot.news_query_guard._last.clear()
            msg = FakeMessage(group_openid=gid)
            asyncio.run(bot.handle_command(msg, None, "group", gid, "/公告 查 明日方舟"))
            check("查指定游戏", _content(msg) == "LATEST_ak_TEST", _content(msg))

            bot.news_query_guard._last.clear()
            msg = FakeMessage(group_openid=gid)
            asyncio.run(bot.handle_command(msg, None, "group", gid, "/公告 舟"))
            check("游戏名快捷查", _content(msg) == "LATEST_ak_TEST")

            bot.news_query_guard._last.clear()
            msg = FakeMessage(group_openid=gid)
            asyncio.run(bot.handle_command(msg, None, "group", gid, "/公告 查"))
            body = _content(msg)
            check("查全部两块", "LATEST_ak_TEST" in body and "LATEST_endfield_TEST" in body)
        finally:
            ark_news.latest_text = original
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# 6. poll_loop 的关闭开关
# ---------------------------------------------------------------------------
def test_poll_loop_disabled():
    print("[6] poll_loop 禁用路径")
    os.environ["QQ_BOT_NEWS_POLL_MINUTES"] = "0"
    try:
        asyncio.run(asyncio.wait_for(ark_news.poll_loop(None, None), timeout=3))
        check("POLL_MINUTES=0 时立即返回", True)
    except asyncio.TimeoutError:
        check("POLL_MINUTES=0 时立即返回", False)
    finally:
        os.environ.pop("QQ_BOT_NEWS_POLL_MINUTES", None)


def main() -> int:
    try:
        test_endfield_extraction()
        test_filter_and_aliases()
        test_push_store()
        test_poll_once()
        test_announce_command()
        test_poll_loop_disabled()
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    print()
    if failures:
        print(f"共 {failures} 项失败")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
