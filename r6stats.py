"""R6 战绩查询：走 R6 Arenyze 的公开 REST API（正规接口，不是爬虫）。

- 数据源：https://public-api.arenyze.com/r6/api
- 鉴权：`api-key` 头（key 放 .env 的 `QQ_BOT_R6_KEY`）
- 能力：段位/RP/最高段位/等级、本赛季与**逐赛季历史**（场次/胜负/KD/时长）、逐干员、
  总时长；RP→段位名用 `/ranks?version=v7` 映射。
- **拿不到「每场对局详情」**：该 API 没有对局列表接口（探测 `/v2/matches` 等均 404），
  只有「逐赛季」与「RP 随时间变化」。想要每场的地图/比分得另找来源（TN/stats.cc 被
  Cloudflare 拦且封爬虫）。

失败一律返回 None / 空串，调用方静默或提示（与 kb/websearch 同策略）。
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

log = logging.getLogger("qqbot.r6stats")

BASE = "https://public-api.arenyze.com/r6/api"
DEFAULT_PLATFORM = "uplay"
TOP_OPERATORS = 3   # 文字版展示的干员数
CARD_OPERATORS = 6  # 图片卡片展示的干员数
SEASON_HISTORY = 3
CACHE_TTL = 300.0  # 秒；同一玩家 5 分钟内不重复请求（省额度）

PLATFORM_ALIASES = {
    "pc": "uplay", "uplay": "uplay", "ubi": "uplay", "steam": "uplay", "ubisoft": "uplay",
    "ps": "psn", "psn": "psn", "ps4": "psn", "ps5": "psn", "playstation": "psn",
    "xbox": "xbl", "xb": "xbl", "xbl": "xbl", "xone": "xbl", "xsx": "xbl", "xboxone": "xbl",
}
PLATFORM_LABEL = {"uplay": "PC", "psn": "PlayStation", "xbl": "Xbox"}

_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_ranks_cache: list | None = None


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


def _get(path: str, **params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"api-key": _key(), "User-Agent": "qqbot-r6/1.0"}
    )
    with urllib.request.urlopen(req, timeout=_timeout()) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


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


def rank_name(rp) -> str | None:
    """按 RP 映射段位名（华北 v7 段位表）。"""
    global _ranks_cache
    if _ranks_cache is None:
        try:
            _ranks_cache = _get("/ranks", version="v7")
        except Exception as exc:  # noqa: BLE001
            log.info("[R6] 段位表获取失败：%s", exc)
            _ranks_cache = []
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
    matches = _num(st.get("matchesPlayed"))
    wins = _num(st.get("matchesWon"))
    losses = _num(st.get("matchesLost"))
    wr = None
    if matches and wins is not None:
        wr = wins / matches * 100.0
    return {
        "code": meta.get("shortName") or meta.get("name") or "",
        "name": meta.get("seasonName") or meta.get("name") or "",
        "rank": _zh_rank(rank_name(rp)),
        "rp": rp,
        "max_rp": _num(st.get("maxRankPoints")),
        "rank_type": meta.get("rankType"),
        "kd": _num(st.get("kdRatio")),
        "matches": matches,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "hours": (_num(st.get("timePlayed")) or 0) / 3600000.0,
        "order": ((seg.get("attributes") or {}).get("season")),
    }


def query(platform: str, name: str) -> dict | None:
    """查一个玩家的战绩；未启用/参数错/失败返回 None。"""
    if not enabled():
        return None
    plat = normalize_platform(platform) or DEFAULT_PLATFORM
    name = (name or "").strip()
    if not name:
        return None
    ck = (plat, name.lower())
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=2) as ex:
            f_prof = ex.submit(_get, "/v2/profile", nameOnPlatform=name, platformType=plat)
            f_full = ex.submit(_get, "/v2/fullstats", nameOnPlatform=name, platformType=plat)
            prof, full = f_prof.result(), f_full.result()
    except Exception as exc:  # noqa: BLE001
        log.info("[R6] 查询失败：%s", exc)
        return None

    account = prof.get("account") or {}
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

    out = {
        "name": name,
        "platform": plat,
        "platform_label": PLATFORM_LABEL.get(plat, plat),
        "avatar": avatar,
        "rank_icon": rank_icon,
        "level": account.get("level"),
        "rank": rank or "未定级",
        "rp": rp,
        "max_rp": max_rp,
        "total_hours": _num((fs.get("totalsHoursPlayed") or {}).get("hours")),
        "season": cur_season,
        "seasons": seasons[1:],  # 历史（新→旧，不含本赛季）
        "peak": peak,
        "rp_history": rp_history,
        "operators": operators[:CARD_OPERATORS],
    }
    _cache[ck] = (time.time(), out)
    return out


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
            rp = f"{int(x['rp'])}RP" if x.get("rp") is not None else "-"
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
            rp = f"{int(x['rp'])} RP" if x.get("rp") is not None else "—"
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
