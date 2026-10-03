"""
明日方舟 / 明日方舟：终末地 官网公告的抓取、过滤与群推送。

数据源（均为官网公开入口，无需鉴权，国内直连，不需要代理）：
  - 明日方舟：GET https://ak.hypergryph.com/api/news?category=LATEST&page=N
      JSON：data.list[] = {cid, tab, title, displayTime(秒级时间戳), brief, ...}
      详情页 https://ak.hypergryph.com/news/{cid}
  - 终末地：GET https://endfield.hypergryph.com/news
      没有公开 JSON API，但官网是 Next.js SSR：公告列表（bulletins 数组，
      字段与明日方舟基本一致，tab 为 "notices"/"events"/...）内嵌在 HTML 的
      self.__next_f.push 数据块里，抓 HTML 提取即可（见 extract_endfield_bulletins）。
      鹰角改版导致解析失败时只影响终末地通道：记日志、下一轮重试，不影响明日方舟。

推送规则：
  - 只推「关键更新」：标题命中关键词（默认 更新|维护|停机|版本，env 可配）；
    活动预告 / 寻访 / 制作组通讯等日常公告不主动打扰，可用 /公告 查 被动查看
  - 一轮轮询里同一游戏有多条新公告 → 合并成一条消息推送
  - **首轮轮询只把现有公告标记为已见、不推送**（避免部署当天把历史公告轰炸一遍）
  - 群主动消息频控（官方文档）：单群 20 条/分钟、1000 条/天——低频公告推送远在
    限内；每群每日推送上限（默认 6，env 可配）只是防解析 bug 刷屏的保险丝
  - 发送失败记日志、不重试（下一轮有新公告自然再推，不堆积补偿）

配置（惰性读取——bot.py 的 load_dotenv() 晚于 import，import 期读 env 只能读到空值）：
  QQ_BOT_NEWS_POLL_MINUTES  轮询间隔分钟（默认 10；设 0 = 关闭主动推送，只保留手动查询）
  QQ_BOT_NEWS_KEYWORDS      关键公告的标题关键词，| 分隔（默认 更新|维护|停机|版本）
  QQ_BOT_NEWS_DAILY_LIMIT   每群每日主动推送上限（默认 6）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass

import aiohttp

log = logging.getLogger("qqbot.news")

# ---------------------------------------------------------------------------
# 游戏表：alias 用于 /公告 指令的参数解析（lower 后精确匹配）
# ---------------------------------------------------------------------------
GAMES: dict[str, dict] = {
    "ak": {
        "name": "明日方舟",
        "aliases": ("明日方舟", "方舟", "舟", "舟游", "arknights", "ak"),
    },
    "endfield": {
        "name": "终末地",
        "aliases": ("终末地", "明日方舟终末地", "明日方舟：终末地", "endfield", "ef"),
    },
}

DEFAULT_KEYWORDS = "更新|维护|停机|版本"
DEFAULT_POLL_MINUTES = 10.0
DEFAULT_DAILY_LIMIT = 6

# 一次轮询最多拉几页列表（明日方舟每页 6 条；终末地一页全量）。两页足以覆盖
# 任意轮询间隔内的全部新公告，再多只是浪费流量。
AK_PAGES = 2

# 被动查询 /公告 查 默认展示条数
LATEST_LIMIT = 5

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
}
_TIMEOUT = aiohttp.ClientTimeout(total=20)


def _poll_minutes() -> float:
    try:
        return float(os.getenv("QQ_BOT_NEWS_POLL_MINUTES", "") or DEFAULT_POLL_MINUTES)
    except ValueError:
        return DEFAULT_POLL_MINUTES


def _keywords_pattern() -> re.Pattern:
    words = (os.getenv("QQ_BOT_NEWS_KEYWORDS", "") or DEFAULT_KEYWORDS).strip()
    if not words:
        words = DEFAULT_KEYWORDS
    return re.compile("|".join(re.escape(w) for w in words.split("|") if w))


def _daily_limit() -> int:
    try:
        return int(os.getenv("QQ_BOT_NEWS_DAILY_LIMIT", "") or DEFAULT_DAILY_LIMIT)
    except ValueError:
        return DEFAULT_DAILY_LIMIT


def resolve_game(word: str) -> str | None:
    """把指令参数解析成游戏 id；认不出返回 None。大小写不敏感。"""
    w = (word or "").strip().lower()
    if not w:
        return None
    for game, conf in GAMES.items():
        if w in conf["aliases"]:
            return game
    return None


# ---------------------------------------------------------------------------
# 数据模型与抓取
# ---------------------------------------------------------------------------
@dataclass
class NewsItem:
    game: str
    cid: str
    title: str
    ts: int  # 秒级时间戳（官网 displayTime）
    tab: str
    brief: str

    @property
    def url(self) -> str:
        base = (
            "https://ak.hypergryph.com/news/"
            if self.game == "ak"
            else "https://endfield.hypergryph.com/news/"
        )
        return base + self.cid


def _item_from_raw(game: str, raw: dict) -> NewsItem | None:
    """官网 JSON 条目 -> NewsItem；缺关键字段的脏数据直接丢弃。"""
    if not isinstance(raw, dict):
        return None
    cid = str(raw.get("cid") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not cid or not title:
        return None
    try:
        ts = int(raw.get("displayTime") or 0)
    except (TypeError, ValueError):
        ts = 0
    return NewsItem(
        game=game,
        cid=cid,
        title=title,
        ts=ts,
        tab=str(raw.get("tab") or ""),
        brief=str(raw.get("brief") or ""),
    )


async def _fetch_ak(session: aiohttp.ClientSession) -> list[NewsItem]:
    """明日方舟：官方 JSON API，拉前 AK_PAGES 页。"""
    items: list[NewsItem] = []
    seen: set[str] = set()
    for page in range(1, AK_PAGES + 1):
        url = f"https://ak.hypergryph.com/api/news?category=LATEST&page={page}"
        async with session.get(url) as resp:
            resp.raise_for_status()
            payload = json.loads(await resp.text())
        rows = ((payload or {}).get("data") or {}).get("list") or []
        for raw in rows:
            item = _item_from_raw("ak", raw)
            if item and item.cid not in seen:
                seen.add(item.cid)
                items.append(item)
    return items


def extract_endfield_bulletins(html: str) -> list[dict]:
    """
    从终末地官网 news 页 HTML 里提取 bulletins 公告数组。

    页面是 Next.js App Router：数据以 RSC flight 格式分散在多个
    `self.__next_f.push([1,"..."])` 内联块里，字符串参数是 JSON 转义过的
    片段。做法：逐块用 json.loads 反转义（转义规则与 JSON 字符串一致），
    拼回完整 flight 流，再定位 "bulletins":[ 并做括号匹配截出数组。

    单独暴露这个函数是为了能离线单测（用真实结构的样本 HTML）。
    """
    chunks: list[str] = []
    for m in re.finditer(r"self\.__next_f\.push\(\[1,\s*(\"(?:[^\"\\]|\\.)*\")\]\)", html):
        try:
            chunks.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue  # 单块损坏就跳过，bulletins 通常完整落在某一块里
    flight = "\n".join(chunks)

    anchor = flight.find('"bulletins":')
    if anchor < 0:
        raise ValueError("HTML 里找不到 bulletins 数据（官网结构可能已改版）")
    start = flight.find("[", anchor)
    if start < 0:
        raise ValueError("bulletins 数组起点异常")

    # 括号匹配截取 JSON 数组：要跳过字符串里的 ] } " 和转义 \"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(flight)):
        ch = flight[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                data = json.loads(flight[start : i + 1])
                if not isinstance(data, list):
                    raise ValueError("bulletins 不是数组")
                return data
    raise ValueError("bulletins 数组没有正常闭合")


async def _fetch_endfield(session: aiohttp.ClientSession) -> list[NewsItem]:
    """终末地：抓官网 news 页 HTML，从内嵌数据里提取公告。"""
    async with session.get("https://endfield.hypergryph.com/news") as resp:
        resp.raise_for_status()
        html = await resp.text()
    rows = extract_endfield_bulletins(html)
    items = []
    seen: set[str] = set()
    for raw in rows:
        item = _item_from_raw("endfield", raw)
        if item and item.cid not in seen:
            seen.add(item.cid)
            items.append(item)
    return items


async def fetch_game(session: aiohttp.ClientSession, game: str) -> list[NewsItem]:
    """拉某个游戏的公告列表。失败抛异常，由调用方决定记日志还是给用户报错。"""
    if game == "ak":
        return await _fetch_ak(session)
    if game == "endfield":
        return await _fetch_endfield(session)
    raise ValueError(f"未知游戏: {game}")


# ---------------------------------------------------------------------------
# 过滤与格式化
# ---------------------------------------------------------------------------
def is_update_news(title: str, pattern: re.Pattern | None = None) -> bool:
    """标题是否属于「关键更新」公告（版本更新 / 维护 / 停机等）。"""
    return bool((pattern or _keywords_pattern()).search(title or ""))


def _fmt_time(ts: int) -> str:
    if ts <= 0:
        return "时间未知"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def format_push_text(game: str, items: list[NewsItem]) -> str:
    """主动推送的正文：同轮多条合并为一条。"""
    lines = [f"【{GAMES[game]['name']}】更新公告"]
    for it in items:
        lines.append(f"· {it.title}")
        lines.append(f"  {_fmt_time(it.ts)}")
        lines.append(f"  {it.url}")
    return "\n".join(lines)


def format_latest_text(game: str, items: list[NewsItem], limit: int = LATEST_LIMIT) -> str:
    """被动查询 /公告 查 的正文：不过滤类型，直接列最新几条。"""
    if not items:
        return f"【{GAMES[game]['name']}】暂时没拉到公告，稍后再试。"
    lines = [f"【{GAMES[game]['name']}】最新公告"]
    for it in items[:limit]:
        lines.append(f"· {it.title}")
        lines.append(f"  {_fmt_time(it.ts)}　{it.url}")
    return "\n".join(lines)


async def latest_text(game: str, limit: int = LATEST_LIMIT) -> str:
    """被动查询入口：现拉一次并格式化。网络失败抛异常（调用方回复错误文案）。"""
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
        items = await fetch_game(session, game)
    return format_latest_text(game, items, limit)


# ---------------------------------------------------------------------------
# 轮询与推送
# ---------------------------------------------------------------------------
# 每群每日已推送条数：{(group_openid, "YYYY-MM-DD"): n}。进程内计数即可——
# 这是防 bug 刷屏的保险丝，不是审计数据，重启清零无妨。
_daily_pushed: dict[tuple[str, str], int] = {}


async def poll_loop(api, pstore) -> None:
    """
    常驻轮询：bot.py 在 on_ready 里 asyncio.create_task 启动（那时事件循环已运行）。

    api 是 botpy Client 的 self.api，用于不带 msg_id 的主动 post_group_message。
    """
    minutes = _poll_minutes()
    if minutes <= 0:
        log.info("[公告] 主动推送未启用（QQ_BOT_NEWS_POLL_MINUTES=0），仅保留手动查询")
        return

    log.info(
        "[公告] 轮询已启动：间隔 %.0f 分钟，关键词=%s",
        minutes,
        _keywords_pattern().pattern,
    )
    first_round = True
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
        while True:
            try:
                await poll_once(session, api, pstore, first_round=first_round)
            except Exception:  # noqa: BLE001
                # 单个游戏拉取失败在 poll_once 里已记日志；这里兜住其余意外，
                # 保证轮询循环永不退出
                log.exception("[公告] 本轮轮询异常")
            first_round = False
            await asyncio.sleep(minutes * 60)


async def poll_once(
    session: aiohttp.ClientSession, api, pstore, first_round: bool
) -> None:
    """拉全部游戏 → 找出新公告 → 关键更新推给开启的群。"""
    for game in GAMES:
        try:
            items = await fetch_game(session, game)
        except Exception as exc:  # noqa: BLE001
            log.warning("[公告] 拉取%s失败（下一轮重试）：%s", GAMES[game]["name"], exc)
            continue

        cids = [it.cid for it in items]
        unseen = await pstore.filter_unseen(game, cids)
        if not unseen:
            continue

        unseen_set = set(unseen)
        fresh = [it for it in items if it.cid in unseen_set]
        await pstore.mark_seen(game, unseen)

        if first_round:
            log.info(
                "[公告] 首轮%s：标记 %d 条既有公告为已见，不推送",
                GAMES[game]["name"],
                len(unseen),
            )
            continue

        hits = [it for it in fresh if is_update_news(it.title)]
        if not hits:
            continue
        await push_to_groups(api, pstore, game, format_push_text(game, hits))


async def push_to_groups(api, pstore, game: str, text: str) -> None:
    """把一条推送正文发给所有开启了该游戏的群（主动消息，不带 msg_id）。"""
    limit = _daily_limit()
    day = time.strftime("%Y-%m-%d")

    for gid in await pstore.enabled_groups(game):
        key = (gid, day)
        if _daily_pushed.get(key, 0) >= limit:
            log.warning(
                "[公告] 群 %s 今日推送已达上限 %d 条，跳过（保险丝，防异常刷屏）",
                gid[:10],
                limit,
            )
            continue
        try:
            await api.post_group_message(group_openid=gid, content=text)
            _daily_pushed[key] = _daily_pushed.get(key, 0) + 1
            log.info("[公告] 已推送到群 %s（%s）", gid[:10], GAMES[game]["name"])
        except Exception as exc:  # noqa: BLE001
            # 不重试：可能是机器人被移出群 / 用户关闭了主动消息接收 / 平台抖动，
            # 堆着补偿只会刷屏。这条 cid 已标记已见，不会反复尝试。
            log.warning("[公告] 推送到群 %s 失败：%s", gid[:10], exc)

    # 清掉隔天的计数键，防止字典随时间缓慢膨胀
    for stale in [k for k in _daily_pushed if k[1] != day]:
        _daily_pushed.pop(stale, None)
