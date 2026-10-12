"""R6 战绩查询：走 R6 Arenyze 的公开 REST API（正规接口，不是爬虫）。

- 数据源：https://public-api.arenyze.com/r6/api
- 鉴权：`api-key` 头（key 放 .env 的 `QQ_BOT_R6_KEY`）
- 能力：段位/RP/最高段位/等级、本赛季与**逐赛季历史**（场次/胜负/KD/时长）、逐干员、
  RP→段位名用 `/ranks?version=v7` 映射。
- **拿不到「每场对局详情」**：该 API 没有对局列表接口（探测 `/v2/matches` 等均 404），
  只有「逐赛季」与「RP 随时间变化」。想要每场的地图/比分得另找来源（TN/stats.cc 被
  Cloudflare 拦且封爬虫）。

失败一律返回 None / 空串，调用方静默或提示（与 kb/websearch 同策略）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("qqbot.r6stats")

BASE = "https://public-api.arenyze.com/r6/api"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 段位表（/ranks）基本不变，落一份磁盘缓存：429 或临时故障时不至于让所有段位名变空
_RANKS_FILE = os.path.join(BASE_DIR, "data", "r6_ranks.json")
_RANKS_TTL = 7 * 24 * 3600.0
DEFAULT_PLATFORM = "uplay"
TOP_OPERATORS = 3   # 文字版展示的干员数
CARD_OPERATORS = 6  # 图片卡片展示的干员数
SEASON_HISTORY = 3
CACHE_TTL = 300.0  # 秒；同一玩家 5 分钟内不重复请求
# ⚠️ 免费额度是 **2,000 次/月**（另有 20/分、120/时、300/天的突发上限）。
# 每次冷查要打 2 个接口，TTL 越短额度消耗越快——按 5 分钟算，一个被频繁查询的
# 玩家一天就能吃掉几百次。被限流时会记下恢复时间（见 _note_blocked），期间不再白打。

PLATFORM_ALIASES = {
    "pc": "uplay", "uplay": "uplay", "ubi": "uplay", "steam": "uplay", "ubisoft": "uplay",
    "ps": "psn", "psn": "psn", "ps4": "psn", "ps5": "psn", "playstation": "psn",
    "xbox": "xbl", "xb": "xbl", "xbl": "xbl", "xone": "xbl", "xsx": "xbl", "xboxone": "xbl",
}
PLATFORM_LABEL = {"uplay": "PC", "psn": "PlayStation", "xbl": "Xbox"}

_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_ranks_cache: list | None = None
_refreshing: set[tuple[str, str]] = set()   # 正在后台刷新的玩家（去重，别重复打接口）
_refresh_lock = threading.Lock()
_quota: dict = {}           # 最近一次响应里的额度信息（Arenyze 会在响应头里给）
_blocked_until = 0.0        # 被限流时，在这之前不再打接口


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def enabled() -> bool:
    return bool(_env("QQ_BOT_R6_KEY"))


def _key() -> str:
    return _env("QQ_BOT_R6_KEY")


def _timeout() -> float:
    try:
        return float(_env("QQ_BOT_R6_TIMEOUT") or 25)
    except ValueError:
        return 25.0


def normalize_platform(token: str | None) -> str | None:
    return PLATFORM_ALIASES.get((token or "").strip().lower())


class R6NotFound(Exception):
    """Arenyze 明确回了 404：这个平台上没有这个昵称。"""


class R6Unavailable(Exception):
    """接口超时 / 5xx / 网络故障——**不是**「没这个人」。

    这两件事必须分开：Arenyze 会偶发 503（实测同一个人 30 秒前 503、30 秒后正常），
    以前一律回「没查到…检查拼写」，会把用户带到沟里去。
    """


def _get(path: str, **params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"api-key": _key(), "User-Agent": "qqbot-r6/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            _note_quota(resp.headers)
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise R6NotFound(f"HTTP 404 {path}") from exc
        if exc.code == 429:
            _note_blocked(exc.headers)
            raise R6Unavailable(f"数据源限流（{path}）") from exc
        raise R6Unavailable(f"HTTP {exc.code} {path}") from exc
    except Exception as exc:  # 超时 / DNS / 连接被拒 / JSON 坏掉
        raise R6Unavailable(f"{type(exc).__name__}: {exc}") from exc


def _note_quota(headers) -> None:
    """记下 Arenyze 响应头里的额度信息（免费版 20/分、120/时、300/天）。"""
    def g(name: str) -> int:
        try:
            return int(headers.get(name) or 0)
        except (TypeError, ValueError):
            return 0
    day_left = g("X-RateLimit-Day-Remaining")
    with _quota_lock_view():
        _quota.update({
            "day_remaining": day_left,
            "hour_remaining": g("X-RateLimit-Hour-Remaining"),
            "minute_remaining": g("X-RateLimit-Minute-Remaining"),
            "day_reset": headers.get("X-RateLimit-Day-Reset") or "",
        })
    if 0 < day_left <= 30:
        log.warning("[R6] 今日额度只剩 %d 次（免费版 300/天）", day_left)


def _quota_lock_view():
    """额度状态用同一把锁保护（和刷新去重共用即可）。"""
    return _refresh_lock


def _note_blocked(headers) -> None:
    """429 时记下要等多久再打接口（Retry-After），避免继续白打、把窗口限流也耗掉。"""
    global _blocked_until
    try:
        after = float(headers.get("Retry-After") or 0)
    except (TypeError, ValueError):
        after = 0.0
    with _quota_lock_view():
        _blocked_until = time.time() + (after or 60.0)
    log.warning("[R6] 被限流，%.0f 分钟后再试", (after or 60.0) / 60)


def blocked_for() -> float:
    """还在限流中的剩余秒数（0 = 没被限流）。"""
    return max(0.0, _blocked_until - time.time())


# 额度快到见底时，把缓存临时拉长——宁可数据旧一点，也别把额度打光（打光当天就
# 彻底查不了了）。平时就是 CACHE_TTL，用户感知不到。
_STRETCH_TTL = 1800.0
_STRETCH_BELOW = 40


def effective_ttl() -> float:
    """当前该用的缓存时长：额度宽裕 = CACHE_TTL，快见底 = 拉长。"""
    with _quota_lock_view():
        day_left = _quota.get("day_remaining")
    if isinstance(day_left, int) and day_left <= _STRETCH_BELOW:
        return max(CACHE_TTL, _STRETCH_TTL)
    return CACHE_TTL


def quota() -> dict:
    """给日志/排查看的额度快照。"""
    with _quota_lock_view():
        return dict(_quota)


def platform_label(platform: str) -> str:
    return PLATFORM_LABEL.get(platform, platform or "")


def _val(v):
    """从 {value:..} 里取值，或直接返回。"""
    if isinstance(v, dict):
        return v.get("value")
    return v


def _num(v):
    v = _val(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_ranks() -> list:
    """取段位表：**先吃磁盘缓存**（7 天），再联网。

    踩过：段位表原来只在内存里，一旦 /ranks 被限流（HTTP 429）段位名全变 None。
    表本身几个月才动一次，落盘很划算。
    """
    try:
        with open(_RANKS_FILE, encoding="utf-8") as f:
            disk = json.load(f)
        rows = disk.get("rows") or []
        if rows and time.time() - (disk.get("at") or 0) < _RANKS_TTL:
            log.info("[R6] 段位表用磁盘缓存（%d 行）", len(rows))
            return rows
    except (OSError, ValueError):
        pass
    try:
        rows = _get("/ranks", version="v7")
    except Exception as exc:  # noqa: BLE001
        log.info("[R6] 段位表获取失败：%s", exc)
        return []
    try:
        os.makedirs(os.path.dirname(_RANKS_FILE), exist_ok=True)
        with open(_RANKS_FILE, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "rows": rows}, f, ensure_ascii=False)
    except OSError as exc:
        log.info("[R6] 段位表落盘失败：%s", exc)
    return rows


def rank_name(rp) -> str | None:
    """按 RP 映射段位名（Arenyze v7 段位表）。"""
    global _ranks_cache
    if _ranks_cache is None:
        _ranks_cache = _load_ranks()
    rp = _num(rp)
    if rp is None:
        return None
    for row in _ranks_cache:
        try:
            if row.get("min_mmr") <= rp <= row.get("max_mmr"):
                return row.get("name")
        except TypeError:
            continue
    return None


# 段位名中文化（长词在前，避免 "Legend Division" 被 "Legend" 先截）
_RANK_ZH = [
    ("Legend Division", "传奇"), ("Unranked", "未定级"), ("Copper", "紫铜"),
    ("Bronze", "青铜"), ("Silver", "白银"), ("Gold", "黄金"), ("Platinum", "铂金"),
    ("Emerald", "翡翠"), ("Diamond", "钻石"), ("Champion", "冠军"), ("Legend", "传奇"),
]


def _zh_rank(name: str | None) -> str | None:
    """把 "Diamond 3" 这类英文段位名换成中文（保留数字）。"""
    if not name:
        return name
    low = name.lower()
    for en, zh in _RANK_ZH:
        if low.startswith(en.lower()):
            return zh + name[len(en):]
    return name


def _season_of(seg: dict) -> dict:
    st = seg.get("stats") or {}
    meta = seg.get("metadata") or {}
    rp = _num(st.get("rankPoints"))
    max_rp = _num(st.get("maxRankPoints"))
    matches = _num(st.get("matchesPlayed"))
    wins = _num(st.get("matchesWon"))
    losses = _num(st.get("matchesLost"))
    wr = None
    if wins is not None:
        # 分母用「胜 + 负」，不用 matchesPlayed：排位里偶尔有既非胜也非负的场次
        # （放弃 / 无结果），matchesPlayed 会比 wins+losses 多 1，拿场次当分母
        # 会和 R6Tracker 差一个百分点（实测 37 场 17 胜 19 负：我们 46%、TN 47%）。
        denom = wins + losses if losses is not None and wins + losses > 0 else matches
        if denom:
            wr = wins / denom * 100.0
    # 赛季展示的 RP/段位用「赛季最高 RP」（maxRankPoints），这与 Arenyze 网站前端
    # 完全一致——它自己的代码就是 `maxRankPoints || rankPoints`，标题叫 "Peak rank"；
    # 也与 R6Tracker 的 MAX 列逐一对得上（XII-FuHua：2,616 / 2,699 / 2,838）。
    # ⚠️ 别优先 rankPoints：那个值既不是 TN 的 MAX 也不是它的 FINAL（像个中间快照，
    # 实测 2,387 / 2,368 / 2,648，两列都对不上）。
    # RP=1000 是定位赛基准，不算段位。
    shown = max_rp or rp
    rank_txt = _zh_rank(rank_name(shown)) if (shown and shown > 1000) else None
    return {
        "code": meta.get("shortName") or meta.get("name") or "",
        "name": meta.get("seasonName") or meta.get("name") or "",
        "rank": rank_txt,
        "rp": rp,
        "rp_show": shown,
        "max_rp": max_rp,
        "rank_type": meta.get("rankType"),
        "kd": _num(st.get("kdRatio")),
        "matches": matches,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "hours": (_num(st.get("timePlayed")) or 0) / 3600000.0,
        "order": ((seg.get("attributes") or {}).get("season")),
    }


def _fetch_pair(name: str, plat: str, attempts: int = 2):
    """并发查 profile + fullstats 两个接口。

    - 任一接口回 404 → `R6NotFound`（查无此人）
    - 其它失败（503 / 超时 / 网络）→ 隔一会儿重试一次，仍不行则 `R6Unavailable`

    注：实测「不存在的昵称」是一个接口回 404、另一个回 503，所以判定顺序是
    **先看有没有 404**，再看剩下的是不是接口故障。
    """
    import concurrent.futures as _cf

    left = blocked_for()
    if left > 0:
        # 已知还在限流就别白打——省得连分钟/小时窗口也一起耗掉
        raise R6Unavailable(f"限流中（约 {int(left // 60) + 1} 分钟后恢复）")

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        errs: list[Exception] = []
        results: list = []
        with _cf.ThreadPoolExecutor(max_workers=2) as ex:
            futs = [
                ex.submit(_get, "/v2/profile", nameOnPlatform=name, platformType=plat),
                ex.submit(_get, "/v2/fullstats", nameOnPlatform=name, platformType=plat),
            ]
            for f in futs:
                try:
                    results.append(f.result())
                except Exception as exc:  # noqa: BLE001
                    errs.append(exc)
        if any(isinstance(e, R6NotFound) for e in errs):
            raise R6NotFound(f"{plat}/{name}")
        if not errs:
            return results[0], results[1]
        last = errs[0]
        log.info("[R6] 第 %d/%d 次查询失败（%s）%s", attempt, attempts, last,
                 "，重试" if attempt < attempts else "，放弃")
        if attempt < attempts:
            time.sleep(1.5)
    raise R6Unavailable(str(last))


def is_cached(platform: str, name: str) -> bool:
    """这个玩家在当前进程里有缓存吗？

    有缓存 = 走 stale-while-revalidate，**立刻**能出图，不用先回一句「正在查」。
    """
    plat = normalize_platform(platform) or DEFAULT_PLATFORM
    return (plat, (name or "").strip().lower()) in _cache


def _refresh_async(ck, name: str, plat: str) -> None:
    """后台刷新一条过期缓存（stale-while-revalidate）。

    同一玩家同时只跑一个刷新线程；失败就继续沿用旧数据——宁可旧，不可卡。
    """
    with _refresh_lock:
        if ck in _refreshing:
            return
        _refreshing.add(ck)

    def _work():
        try:
            _cache[ck] = (time.time(), _build(plat, name))
        except Exception as exc:  # noqa: BLE001
            log.info("[R6] 后台刷新失败（继续用旧数据）：%s", exc)
        finally:
            with _refresh_lock:
                _refreshing.discard(ck)

    threading.Thread(target=_work, daemon=True, name="r6-refresh").start()


def warm_up() -> None:
    """启动后台预热：段位表（`/ranks`，约 2-3 秒，每个进程只拉一次）。

    不预热的话，**第一个**来查 R6 的人要额外等这一下。
    """
    if not enabled():
        return
    try:
        rank_name(0)
        log.info("[R6] 段位表预热完成")
    except Exception as exc:  # noqa: BLE001
        log.info("[R6] 段位表预热失败：%s", exc)


def query(platform: str, name: str) -> dict | None:
    """查一个玩家的战绩。

    查无此人 → 抛 `R6NotFound`；接口故障 → 抛 `R6Unavailable`；
    未启用 / 昵称为空 → 返回 None。

    缓存策略是 **stale-while-revalidate**：有缓存就立刻返回（哪怕刚过期），
    过期那条丢给后台线程刷新——用户永远不用为「刷新」等 Arenyze。
    于是只有**本进程第一次查某个玩家**才真的慢（Arenyze 要现拉育碧的数据）。
    """
    if not enabled():
        return None
    plat = normalize_platform(platform) or DEFAULT_PLATFORM
    name = (name or "").strip()
    if not name:
        return None
    ck = (plat, name.lower())
    hit = _cache.get(ck)
    if hit:
        if time.time() - hit[0] < effective_ttl():
            return hit[1]
        _refresh_async(ck, name, plat)
        return hit[1]
    out = _build(plat, name)
    _cache[ck] = (time.time(), out)
    return out


def _build(plat: str, name: str) -> dict:
    """真正去 Arenyze 拉两个接口并归一成卡片/文本要的结构（与缓存无关）。"""
    prof, full = _fetch_pair(name, plat)

    account = prof.get("account") or {}
    # 接口会把昵称纠正成规范写法（大小写、别人改名前的老写法），用它更准，
    # 也顺便让 stats.cc / R6Tracker 的链接好看。
    profs = account.get("profiles") or []
    canonical = profs[0].get("nameOnPlatform") if profs and isinstance(profs[0], dict) else None
    # 生涯时长：**官方 / R6Tracker 口径**在 /v2/profile 的 overview 段里（单位：秒）。
    # ⚠️ 千万别用 fullStats.totalsHoursPlayed —— 那是「所有 PvP 模式逐赛季相加的会话
    # 时长」，和 TN/官方差得毫无规律（MacieJay 5887 vs 9305、mojinqiu 388 vs 180）。
    # overview 段实测与 R6Tracker 逐一对得上：mojinqiu 180h、MacieJay 9,305h、XII-FuHua 1,266h。
    sea_segs = ((prof.get("seasons") or {}).get("data") or {}).get("segments") or []
    ov = next((s for s in sea_segs
               if isinstance(s, dict) and s.get("type") == "overview"), None)
    ov_tp = _num(_val(((ov or {}).get("stats") or {}).get("timePlayed")))
    total_hours = round(ov_tp / 3600.0) if ov_tp else None
    # 素材：头像 + 当前段位图标（RP 历史最新一条带 imageUrl）
    avatar = account.get("profilePicture")
    rank_icon = None
    hist_raw = (
        (((prof.get("history") or {}).get("data") or {}).get("history") or {}).get("data")
    ) or []
    if isinstance(hist_raw, list) and hist_raw:
        try:
            rank_icon = (hist_raw[0][1].get("metadata") or {}).get("imageUrl")
        except Exception:  # noqa: BLE001
            rank_icon = None
    # 当前排位面板
    rank = rp = max_rp = None
    boards = ((prof.get("stats") or {}).get("platform_families_full_profiles") or [{}])[0]
    profile_id = boards.get("profile_id")
    for b in boards.get("board_ids_full_profiles") or []:
        if b.get("board_id") == "ranked":
            fp = (b.get("full_profiles") or [{}])[0].get("profile") or {}
            rp, max_rp = _num(fp.get("rank_points")), _num(fp.get("max_rank_points"))
            break
    if rank is None:
        rank = _zh_rank(rank_name(rp))

    fs = full.get("fullStats") or {}
    # 只取「排位 + 具体赛季」的分段：同一赛季还有事件/休闲/标准等分段，混进来会串赛季。
    ranked = [
        s for s in ((fs.get("data") or {}).get("segments") or [])
        if isinstance(s, dict)
        and (s.get("attributes") or {}).get("gamemode") == "pvp_ranked"
        and (s.get("attributes") or {}).get("season") is not None
    ]
    seasons = sorted(
        (_season_of(s) for s in ranked),
        key=lambda x: (x.get("order") if isinstance(x.get("order"), int) else -1),
        reverse=True,  # 新 → 旧
    )
    cur_season = seasons[0] if seasons else None
    if cur_season and cur_season.get("rank") and not rank:
        rank = cur_season["rank"]

    operators = []
    for o in (fs.get("operators") or [])[:50]:
        if not isinstance(o, dict):
            continue
        operators.append({
            "name": o.get("operator") or o.get("operator_name") or "?",
            "kd": _num(o.get("kd")),
            "win": _num(o.get("winPercent")),
            "rounds": _num(o.get("roundsPlayed")) or 0,
        })
    operators.sort(key=lambda x: -(x.get("rounds") or 0))

    # 历史最高：只在「RP 时代」（rank_type=rp）里比——MMR 时代数值不可比
    peak = None
    for x in seasons:
        if x.get("rank_type") != "rp" or x.get("max_rp") is None:
            continue
        if peak is None or x["max_rp"] > peak["rp"]:
            peak = {
                "code": x["code"],
                "rank": _zh_rank(rank_name(x["max_rp"])),
                "rp": x["max_rp"],
            }

    # RP 随时间变化（近一月折线图用）；history 是「新→旧」，翻成「旧→新」
    rp_history = []
    if isinstance(hist_raw, list):
        for e in reversed(hist_raw):
            try:
                v = _num(e[1].get("value"))
            except Exception:  # noqa: BLE001
                v = None
            if v is not None:
                rp_history.append((e[0], v))

    return {
        "name": canonical or name,
        "platform": plat,
        "platform_label": PLATFORM_LABEL.get(plat, plat),
        "avatar": avatar,
        "rank_icon": rank_icon,
        "profile_id": profile_id,
        "level": account.get("level"),
        "rank": rank or "未定级",
        "rp": rp,
        "max_rp": max_rp,
        # 生涯时长（官方口径，见上面 overview 段那段注释）；取不到就不显示
        "total_hours": total_hours,
        "season": cur_season,
        "seasons": seasons[1:],  # 历史（新→旧，不含本赛季）
        "peak": peak,
        "rp_history": rp_history,
        "operators": operators[:CARD_OPERATORS],
    }


def format_reply(q: dict) -> str:
    """把查询结果排成一条可读的文本回复。"""
    if not q:
        return ""
    lines = [f"🎮 R6 战绩 · {q['name']}（{q['platform_label']}）"]

    seg2 = f"段位 {q['rank']}"
    if q.get("rp") is not None:
        seg2 += f" · {int(q['rp'])} RP"
    if q.get("max_rp") is not None:
        seg2 += f"（历史最高 {int(q['max_rp'])}）"
    lines.append(seg2)

    meta = []
    if q.get("level"):
        meta.append(f"等级 {q['level']}")
    if q.get("total_hours"):
        meta.append(f"总时长 {int(q['total_hours'])}h")
    if meta:
        lines.append(" ｜ ".join(meta))

    s = q.get("season")
    if s and s.get("matches"):
        wr = f"{int(s['wins'])}胜{int(s['losses'])}负" if s.get("wins") is not None else ""
        pct = f"（{s['win_rate']:.0f}%）" if s.get("win_rate") is not None else ""
        kd = f" · KD {s['kd']:.2f}" if s.get("kd") is not None else ""
        label = f"{s['code']} {s['name']}".strip()
        lines.append(f"本赛季 {label}：{int(s['matches'])} 场 {wr}{pct}{kd}")

    hist = [x for x in (q.get("seasons") or []) if x.get("matches")][:SEASON_HISTORY]
    if hist:
        parts = []
        for x in hist:
            kd = f"{x['kd']:.2f}" if x.get("kd") is not None else "-"
            rp = f"{int(x['rp_show'])}RP" if x.get("rp_show") is not None else "-"
            parts.append(f"{x['code']} {x.get('rank') or '-'} {rp}/KD{kd}")
        lines.append("近几季：" + " ｜ ".join(parts))

    ops = [o for o in (q.get("operators") or []) if o.get("kd") is not None][:TOP_OPERATORS]
    if ops:
        lines.append("常用干员：" + " · ".join(
            f"{o['name']} KD{o['kd']:.2f}"
            + (f"/胜{o['win']:.0f}%" if o.get("win") is not None else "")
            for o in ops
        ))
    return "\n".join(lines)


def _int_env(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


TN_PLATFORM = {"uplay": "ubi", "psn": "psn", "xbl": "xbl"}


def site_urls(platform: str, name: str, profile_id: str | None = None) -> list[str]:
    """R6Tracker / stats.cc 的个人主页链接（看每场对局等更多历史用）。

    R6Tracker 的实际格式是 `r6.tracker.network/r6siege/profile/<平台>/<昵称>/overview`
    （平台 slug：ubi / psn / xbl —— 注意 PC 是 **ubi** 不是 pc，踩过坑）。
    名字大小写不敏感，用接口给的规范昵称最好。
    """
    from urllib.parse import quote

    tn = TN_PLATFORM.get(platform or "", "ubi")
    n = quote(name or "")
    out = [f"R6Tracker　https://r6.tracker.network/r6siege/profile/{tn}/{n}/overview"]
    if profile_id:
        out.append(f"stats.cc　　https://stats.cc/siege/{n}/{quote(profile_id)}")
    return out


def image_enabled() -> bool:
    return (_env("QQ_BOT_R6_IMAGE") or "1").lower() in {"1", "true", "on", "yes"}


def format_card_text(q: dict) -> str:
    """战绩的**卡片版**文本（比聊天纯文本更整齐，用于渲染成图）。"""
    if not q:
        return ""
    lines = ["【R6 战绩】", "", f"{q['name']}（{q['platform_label']}）"]

    seg = f"段位 {q['rank']}"
    if q.get("rp") is not None:
        seg += f" · {int(q['rp'])} RP"
    if q.get("max_rp") is not None:
        seg += f"（最高 {int(q['max_rp'])}）"
    lines.append(seg)

    meta = []
    if q.get("level"):
        meta.append(f"等级 {q['level']}")
    if q.get("total_hours"):
        meta.append(f"总时长 {int(q['total_hours'])}h")
    if meta:
        lines.append("　".join(meta))

    s = q.get("season")
    if s and s.get("matches"):
        wr = f"{int(s['wins'])}胜{int(s['losses'])}负" if s.get("wins") is not None else ""
        pct = f"（{s['win_rate']:.0f}%）" if s.get("win_rate") is not None else ""
        kd = f" · KD {s['kd']:.2f}" if s.get("kd") is not None else ""
        lines += ["", f"本赛季 {s['code']} {s['name']}".rstrip(),
                  f"　{int(s['matches'])} 场 {wr}{pct}{kd}"]

    hist = [x for x in (q.get("seasons") or []) if x.get("matches")][:SEASON_HISTORY]
    if hist:
        lines += ["", "近几季"]
        for x in hist:
            kd = f"KD{x['kd']:.2f}" if x.get("kd") is not None else "KD —"
            rp = f"{int(x['rp_show'])} RP" if x.get("rp_show") is not None else "—"
            lines.append(f"　{x['code']}　{x.get('rank') or '—'}　{rp}　{kd}")

    ops = [o for o in (q.get("operators") or []) if o.get("kd") is not None][:TOP_OPERATORS]
    if ops:
        lines += ["", "常用干员"]
        for o in ops:
            w = f" · 胜{o['win']:.0f}%" if o.get("win") is not None else ""
            lines.append(f"　{o['name']}　KD{o['kd']:.2f}{w}")
    return "\n".join(lines)


def render_card(q: dict) -> bytes | None:
    """把战绩渲染成图片：优先 PIL 卡片，失败退回「文字排版转图」，再不行返回 None。"""
    if not q:
        return None
    try:
        import r6_card  # 懒导入：无 PIL 的环境（如本地离线测试）不会因此炸掉整个模块
        data = r6_card.render(q)
        if data:
            return data
    except Exception as exc:  # noqa: BLE001
        log.warning("[R6] 卡片渲染失败：%s", exc)
    try:
        from text_image import render_text_image
        return render_text_image(
            format_card_text(q),
            width=_int_env("QQ_BOT_R6_WIDTH", 900),
            font_size=_int_env("QQ_BOT_R6_FONTSIZE", 30),
            padding=36,
            line_spacing=1.5,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[R6] 文字图渲染失败，退回文本：%s", exc)
        return None
