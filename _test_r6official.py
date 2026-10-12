"""育碧官方 API 客户端（r6official）：开关、登录票据缓存、解析、失败降级。

离线测试：把网络层 `_request` 整个换成假实现（curl_cffi 只在服务器上装）。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import r6official as ro  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


LOGIN = {"ticket": "T" * 40, "profileId": "pid", "spaceId": ro.XPLAY_SPACE}
STATS = {"profiles": [{"profileId": "uid-1", "stats": {
    "PTotalTimePlayed": {"value": 9383451},
    "PPvPTimePlayed": {"value": 9310000},
    "PvETimePlayed": {"value": 73451},
    "PClearanceLevel": {"value": 918},
}}]}
FULL = {"platform_families_full_profiles": [{"board_ids_full_profiles": [
    {"board_id": "standard", "full_profiles": [{"profile": {"rank_points": 0}}]},
    {"board_id": "ranked", "full_profiles": [{"profile": {
        "rank_points": 1916, "max_rank_points": 1984,
        "wins": 17, "losses": 19, "kills": 200, "deaths": 213}}]},
]}]}

calls: list = []


def fake_request(method, url, headers, body=None):
    calls.append((method, url, headers.get("Authorization", "")))
    if url.endswith("/profiles/sessions"):
        return LOGIN
    if "/v1/profiles/stats" in url:
        return STATS
    if "full_profiles" in url:
        return FULL
    raise AssertionError(f"未预期的 URL {url}")


ro._request = fake_request
ro._ticket, ro._ticket_at = None, 0.0
for k in ("QQ_BOT_UBI_EMAIL", "QQ_BOT_UBI_PASSWORD", "QQ_BOT_UBI_PROXY"):
    os.environ.pop(k, None)

check("什么都没配 → 不启用", not ro.enabled())
os.environ["QQ_BOT_UBI_EMAIL"] = "a@b.c"
os.environ["QQ_BOT_UBI_PASSWORD"] = "pw"
check("只有邮箱密码、没代理 → 仍不启用（没代理必 429）", not ro.enabled())
os.environ["QQ_BOT_UBI_PROXY"] = "socks5://127.0.0.1:1080"
check("三项配齐 → 启用", ro.enabled())

p = ro.playtime("uid-1")
check("时长解析：总/PvP/PvE/等级",
      p == {"total_seconds": 9383451, "pvp_seconds": 9310000,
            "pve_seconds": 73451, "level": 918}, str(p))
check("换算成小时（2606.5h）", p and round(p["total_seconds"] / 3600, 1) == 2606.5)

r = ro.ranked("uid-1")
check("段位解析：RP/最高/胜负",
      bool(r) and r["rank_points"] == 1916 and r["max_rank_points"] == 1984
      and r["wins"] == 17 and r["losses"] == 19, str(r))
check("ranked 板而不是 standard",
      any("full_profiles" in u for _, u, _ in calls))

posts = [c for c in calls if c[0] == "POST"]
check("票据只登录一次（进程内缓存）", len(posts) == 1, f"{len(posts)} 次登录")
check("查询都带票据", all(auth.startswith("Ubi_v1 t=") for m, _, auth in calls if m == "GET"))


def boom(method, url, headers, body=None):
    raise RuntimeError("HTTP 429: Too many calls per IP address")


ro._request = boom
ro._ticket, ro._ticket_at = None, 0.0
check("接口报错 → 返回 None，不抛异常（好让 Arenyze 兜底）",
      ro.playtime("uid-1") is None and ro.ranked("uid-1") is None)

print(f"\n失败 {failures} 项")
sys.exit(1 if failures else 0)
