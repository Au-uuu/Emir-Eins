"""育碧**官方** R6 API：游戏时长 / 等级 / 段位。

为什么需要它：Arenyze 的 `totalsHoursPlayed` 是「全模式逐赛季相加的会话时长」，
和 R6Tracker / 官方口径差得**没有规律**（MacieJay 5887h vs 9305h、
Beaulo 4794h vs 10557h、mojinqiu 388h vs 180h），所以「时长」这项只能用官方数据。

为什么必须走代理：本机是腾讯云**大陆机房 IP**，育碧按 IP 限流
（`errorCode 1101 · Too many calls per IP address`），连登录都 429；
不带鉴权的裸请求反而是 400（说明不是全网封 IP，是这个 IP 的配额被掐）。
配好 `QQ_BOT_UBI_PROXY` 后所有请求走代理。

为什么用 curl_cffi：这条链路前面有 **DataDome** 反爬（普通 curl 直接吃 JS 挑战），
需要浏览器的 TLS 指纹。

对外只有两个同步函数：`playtime(uid)` / `ranked(uid)`。
调用方（r6stats）在自己的工作线程里调，所以这里不做 async。

⚠️ 官方 `player_skill_records`（逐赛季段位）**只覆盖旧 MMR 时代（赛季 6–27）**，
RP 时代（Y8 起）没有——所以逐赛季历史仍得用 Arenyze 的 segments。

⚠️ 现状：这条路**暂时搁置**（用不上了）。准确时长其实在 Arenyze 的
`/v2/profile → seasons.data.segments[type=overview].stats.timePlayed` 里，
不需要官方 API。凭据已从服务器 `.env` 清掉，`curl_cffi` 也已卸装。
真要启用时：`pip install curl_cffi`，再配
`QQ_BOT_UBI_EMAIL` / `QQ_BOT_UBI_PASSWORD` / `QQ_BOT_UBI_PROXY`
（必须走代理——机房 IP 会被育碧按 IP 限流 429 连登录都过不去）；
`enabled()` 要求三项齐全才生效，缺任一项就完全不动。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time

log = logging.getLogger("qqbot.r6official")

BASE = "https://public-ubiservices.ubi.com"
# siegeapi 用的 R6 应用 id（合法、公开）
APPID = "e3d5ea9e-50bd-43b7-88bf-39794f4e3d40"
XPLAY_SPACE = "0d2ae42d-4c27-4cb7-af6c-2099062302bb"

TIMEOUT = 25.0
TICKET_TTL = 1800.0  # 票据缓存半小时（育碧一般给几小时，保守些）

_ticket: str | None = None
_ticket_at = 0.0
_lock = threading.Lock()


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def enabled() -> bool:
    """三个都配齐才启用：邮箱 + 密码 + 代理。

    故意把代理也算进前置条件——没有代理的话这个 IP 必然 429，
    不如干脆不试，直接走 Arenyze。
    """
    return bool(
        _env("QQ_BOT_UBI_EMAIL")
        and _env("QQ_BOT_UBI_PASSWORD")
        and _env("QQ_BOT_UBI_PROXY")
    )


def _proxies() -> dict[str, str] | None:
    p = _env("QQ_BOT_UBI_PROXY")
    return {"http": p, "https": p} if p else None


def _request(method: str, url: str, headers: dict, body: dict | None = None):
    """真正的网络层。单独抽出来，测试里可以整个换掉。"""
    import curl_cffi.requests as cr  # 懒加载：没装也不影响 Arenyze 那条路

    fn = cr.post if method == "POST" else cr.get
    kw = {"headers": headers, "impersonate": "chrome", "timeout": TIMEOUT}
    if body is not None:
        kw["json"] = body
    px = _proxies()
    if px:
        kw["proxies"] = px
    resp = fn(url, **kw)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
    return resp.json()


def _login(force: bool = False) -> str:
    """登录换票据（进程内缓存）。"""
    global _ticket, _ticket_at
    with _lock:
        if not force and _ticket and time.time() - _ticket_at < TICKET_TTL:
            return _ticket
        email, pwd = _env("QQ_BOT_UBI_EMAIL"), _env("QQ_BOT_UBI_PASSWORD")
        basic = base64.b64encode(f"{email}:{pwd}".encode("utf-8")).decode("ascii")
        data = _request(
            "POST",
            f"{BASE}/v3/profiles/sessions",
            {"Ubi-AppId": APPID, "Authorization": f"Basic {basic}",
             "Content-Type": "application/json"},
            {"rememberMe": True},
        )
        ticket = data.get("ticket")
        if not ticket:
            raise RuntimeError(f"登录没拿到 ticket：{json.dumps(data)[:160]}")
        _ticket, _ticket_at = ticket, time.time()
        log.info("[R6官方] 登录成功（ticket %d 字符）", len(ticket))
        return ticket


def _authed_get(url: str) -> dict:
    """带票据 GET；票据过期会自动重新登录一次。"""
    for attempt in (1, 2):
        ticket = _login(force=attempt == 2)
        try:
            return _request("GET", url, {"Ubi-AppId": APPID,
                                         "Authorization": f"Ubi_v1 t={ticket}"})
        except RuntimeError as exc:
            if attempt == 2 or "401" not in str(exc):
                raise
            log.info("[R6官方] 票据失效，重新登录：%s", exc)
    raise RuntimeError("unreachable")


def _stat(stats: dict, key: str) -> int:
    val = stats.get(key)
    if isinstance(val, dict):
        val = val.get("value")
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def playtime(uid: str) -> dict | None:
    """官方时长 + 等级。

    Returns: {"total_seconds", "pvp_seconds", "pve_seconds", "level"}；失败返回 None。
    """
    url = (f"{BASE}/v1/profiles/stats?profileIds={uid}&spaceId={XPLAY_SPACE}"
           f"&statNames=PPvPTimePlayed,PPvETimePlayed,PTotalTimePlayed,PClearanceLevel")
    try:
        data = _authed_get(url)
    except Exception as exc:  # noqa: BLE001
        log.info("[R6官方] 时长查询失败：%s", exc)
        return None
    profiles = data.get("profiles") or []
    stats = (profiles[0].get("stats") or {}) if profiles and isinstance(profiles[0], dict) else {}
    if not stats:
        return None
    return {
        "total_seconds": _stat(stats, "PTotalTimePlayed"),
        "pvp_seconds": _stat(stats, "PPvPTimePlayed"),
        "pve_seconds": _stat(stats, "PvETimePlayed"),
        "level": _stat(stats, "PClearanceLevel"),
    }


def ranked(uid: str, platform_group: str = "pc") -> dict | None:
    """官方当前段位面板（ranked 板）。

    Returns: {"rank_points", "max_rank_points", "wins", "losses", "kills", "deaths"}；失败 None。
    """
    fam = (platform_group or "pc").lower()
    url = (f"{BASE}/v2/spaces/{XPLAY_SPACE}/title/r6s/skill/full_profiles"
           f"?profile_ids={uid}&platform_families={fam}")
    try:
        data = _authed_get(url)
    except Exception as exc:  # noqa: BLE001
        log.info("[R6官方] 段位查询失败：%s", exc)
        return None
    fams = data.get("platform_families_full_profiles") or []
    boards = (fams[0].get("board_ids_full_profiles") or []) if fams else []
    for board in boards:
        if board.get("board_id") != "ranked":
            continue
        prof = ((board.get("full_profiles") or [{}])[0].get("profile") or {})
        return {
            "rank_points": _stat(prof, "rank_points"),
            "max_rank_points": _stat(prof, "max_rank_points"),
            "wins": _stat(prof, "wins"),
            "losses": _stat(prof, "losses"),
            "kills": _stat(prof, "kills"),
            "deaths": _stat(prof, "deaths"),
        }
    return None
