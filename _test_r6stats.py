"""R6 战绩查询（r6stats）：平台归一、段位映射、查询解析、失败降级、缓存、排版。

零依赖、不联网：把 r6stats._get 换成假实现即可测。
"""
import os
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import r6stats  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


RANKS = [
    {"name": "Unranked", "min_mmr": 0, "max_mmr": 999},
    {"name": "Platinum 1", "min_mmr": 4000, "max_mmr": 4299},
    {"name": "Diamond 2", "min_mmr": 4300, "max_mmr": 4399},
    {"name": "Diamond 1", "min_mmr": 4400, "max_mmr": 4599},
]
PROFILE = {
    "player": {"nameOnPlatform": "MacieJay", "platformType": "uplay"},
    "stats": {"platform_families_full_profiles": [{"profile_id": "pid-test", "board_ids_full_profiles": [
        {"board_id": "standard", "full_profiles": [{"profile": {"rank_points": 0}}]},
        {"board_id": "ranked", "full_profiles": [{"profile": {
            "rank_points": 4347, "max_rank_points": 4442}}]},
    ]}]},
    "account": {"level": 3512, "profiles": [
        {"platformType": "uplay", "nameOnPlatform": "MacieJay"}]},
    "history": {"data": {"history": {"data": [
        ["2026-10-02T10:00:00+00:00", {"value": 4347, "metadata": {"rank": "Diamond 2"}}],
        ["2026-10-01T10:00:00+00:00", {"value": 4000, "metadata": {"rank": "Diamond 2"}}],
    ]}}},
}
FULL = {"fullStats": {
    "data": {"metadata": {"currentSeason": 43}, "segments": [
        {"stats": {"rankPoints": {"value": 4000}, "maxRankPoints": {"value": 4000},
                   "kdRatio": {"value": 1.05},
                   "matchesPlayed": {"value": 200}, "matchesWon": {"value": 100},
                   "matchesLost": {"value": 99}},
         "metadata": {"shortName": "Y11S2", "seasonName": "Old", "rankType": "rp"},
         "attributes": {"season": 42, "gamemode": "pvp_ranked"}},
        {"stats": {"rankPoints": {"value": 4347}, "maxRankPoints": {"value": 4442},
                   "kdRatio": {"value": 1.08},
                   "matchesPlayed": {"value": 220}, "matchesWon": {"value": 112},
                   "matchesLost": {"value": 107}, "timePlayed": {"value": 1331994718}},
         "metadata": {"shortName": "Y11S3", "seasonName": "Split Fire", "rankType": "rp"},
         "attributes": {"season": 43, "gamemode": "pvp_ranked"}},
    ]},
    "operators": [
        {"operator": "Pulse", "kd": 1.39, "winPercent": 55.03, "roundsPlayed": 2348},
        {"operator": "Jager", "kd": 1.10, "winPercent": 52.0, "roundsPlayed": 900},
    ],
    "totalsHoursPlayed": {"hours": 5706},
}}

calls = {"n": 0}

OPERATORS = {"operators": {"operators": [
    {"operator": "Pulse", "kd": 1.39, "winPercent": 55.0, "roundsPlayed": 2348,
     "kills": 1000, "deaths": 700, "headshots": 400, "assists": 200},
]}}


def fake_get(path, **params):
    calls["n"] += 1
    if path == "/ranks":
        return RANKS
    if path == "/v2/profile":
        return PROFILE
    if path == "/v2/fullstats":
        return FULL
    if path == "/v2/operators":
        return OPERATORS
    raise AssertionError(f"未预期的路径 {path}")


def boom_get(path, **params):
    raise OSError("网络挂了")


_REAL_GET = r6stats._get          # 打桩前留一份真身，后面测 HTTP 分类要用
r6stats._get = fake_get
r6stats._cache.clear()
r6stats._ranks_cache = None

check("平台归一 pc→uplay", r6stats.normalize_platform("PC") == "uplay")
check("平台归一 ps5→psn", r6stats.normalize_platform("ps5") == "psn")
check("平台归一 xb→xbl", r6stats.normalize_platform("xb") == "xbl")
check("未知平台→None", r6stats.normalize_platform("switch") is None)

check("段位映射 4347→Diamond 2", r6stats.rank_name(4347) == "Diamond 2")
check("段位映射 0→Unranked", r6stats.rank_name(0) == "Unranked")
check("段位中文化 Diamond 3→钻石 3", r6stats._zh_rank("Diamond 3") == "钻石 3")
check("段位中文化 Legend Division→传奇", r6stats._zh_rank("Legend Division") == "传奇")

os.environ.pop("QQ_BOT_R6_KEY", None)
check("未配置 key → query 返回 None", r6stats.query("uplay", "MacieJay") is None)

os.environ["QQ_BOT_R6_KEY"] = "test-key"
os.environ["QQ_BOT_R6_TIMEOUT"] = "5"
check("已启用", r6stats.enabled() is True)

q = r6stats.query("pc", "MacieJay")
check("查询返回结果", isinstance(q, dict))
check("解析段位/RP", q and q["rank"] == "钻石 2" and q["rp"] == 4347)
check("解析最高 RP", q and q["max_rp"] == 4442)
check("历史最高（RP 时代）", q and q.get("peak") and q["peak"]["code"] == "Y11S3"
      and int(q["peak"]["rp"]) == 4442)
check("RP 序列旧→新", q and [int(v) for _, v in q["rp_history"]] == [4000, 4347])
check("解析 profile_id（stats.cc 链接用）", q and q.get("profile_id") == "pid-test")
check("昵称取接口的规范写法（大小写纠正）",
      (r6stats.query("pc", "MACIEJAY") or {}).get("name") == "MacieJay")
check("平台中文标签 pc→PC / ps→PlayStation",
      r6stats.platform_label("uplay") == "PC" and r6stats.platform_label("psn") == "PlayStation")

urls = r6stats.site_urls("uplay", "MacieJay", "pid-test")
check("R6Tracker 链接", any("r6.tracker.network/profile/pc/MacieJay" in u for u in urls), str(urls))
check("stats.cc 链接", any("stats.cc/siege/MacieJay/pid-test" in u for u in urls), str(urls))
check("xbl → xbox 平台映射",
      any("/profile/xbox/X" in u for u in r6stats.site_urls("xbl", "X", None)))
check("无 profile_id 时只给 R6Tracker", len(r6stats.site_urls("uplay", "X", None)) == 1)
check("解析等级/总时长", q and q["level"] == 3512 and q["total_hours"] == 5706)
check("本赛季取最新（Y11S3）", q and q["season"]["code"] == "Y11S3")
check("本赛季胜负/胜率", q and q["season"]["wins"] == 112 and q["season"]["losses"] == 107
      and abs(q["season"]["win_rate"] - 50.9) < 1)
check("历史赛季不含本赛季", q and all(s["code"] != "Y11S3" for s in q["seasons"]))
check("干员按回合排序", q and q["operators"][0]["name"] == "Pulse")

text = r6stats.format_reply(q)
check("排版含昵称/段位", "MacieJay" in text and "钻石 2" in text)
check("排版含本赛季 KD", "Y11S3" in text and "KD 1.08" in text)
check("排版含常用干员", "Pulse" in text)

card = r6stats.format_card_text(q)
check("卡片含姓名/段位/本赛季", "MacieJay" in card and "钻石 2" in card and "Y11S3" in card)
check("卡片含近几季/常用干员", "近几季" in card and "常用干员" in card and "Pulse" in card)

img = r6stats.render_card(q)
check("render_card 不抛异常（有 PIL 则出 PNG）",
      img is None or (isinstance(img, bytes) and img[:4] == b"\x89PNG"))

before = calls["n"]
r6stats.query("pc", "MacieJay")
check("5 分钟内命中缓存不重复请求", calls["n"] == before, f"{before}->{calls['n']}")

r6stats._get = boom_get
r6stats._cache.clear()

# --- 查无此人 vs 接口抽风：必须分开，否则 503 会被说成「你昵称写错了」 ---


def notfound_get(path, **params):
    raise r6stats.R6NotFound("HTTP 404 fake")


r6stats._get = notfound_get
r6stats._cache.clear()
try:
    r6stats.query("uplay", "Nobody")
    check("404 → 抛 R6NotFound", False)
except r6stats.R6NotFound:
    check("404 → 抛 R6NotFound", True)
except Exception as exc:  # noqa: BLE001
    check("404 → 抛 R6NotFound", False, repr(exc))

r6stats._get = boom_get
r6stats._cache.clear()
try:
    r6stats.query("uplay", "Anyone")
    check("网络/503 → 抛 R6Unavailable", False)
except r6stats.R6Unavailable:
    check("网络/503 → 抛 R6Unavailable", True)
except Exception as exc:  # noqa: BLE001
    check("网络/503 → 抛 R6Unavailable", False, repr(exc))

# _get 自己也得按 HTTP 状态码分类
_real_urlopen = urllib.request.urlopen


def _fake_urlopen_code(code):
    def _open(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
    return _open


for _code, _want in ((404, r6stats.R6NotFound), (503, r6stats.R6Unavailable)):
    urllib.request.urlopen = _fake_urlopen_code(_code)
    try:
        _REAL_GET("/v2/profile", nameOnPlatform="X", platformType="uplay")
        check(f"_get HTTP {_code} → {_want.__name__}", False)
    except _want:
        check(f"_get HTTP {_code} → {_want.__name__}", True)
    except Exception as exc:  # noqa: BLE001
        check(f"_get HTTP {_code} → {_want.__name__}", False, repr(exc))
urllib.request.urlopen = _real_urlopen

print(f"\n{'=' * 50}")
print(f"失败 {failures} 项")
sys.exit(1 if failures else 0)
