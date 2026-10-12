"""R6 战绩查询（r6stats）：平台归一、段位映射、查询解析、失败降级、缓存、排版。

零依赖、不联网：把 r6stats._get 换成假实现即可测。
"""
import os
import sys

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
    "stats": {"platform_families_full_profiles": [{"board_ids_full_profiles": [
        {"board_id": "standard", "full_profiles": [{"profile": {"rank_points": 0}}]},
        {"board_id": "ranked", "full_profiles": [{"profile": {
            "rank_points": 4347, "max_rank_points": 4442}}]},
    ]}]},
    "account": {"level": 3512},
}
FULL = {"fullStats": {
    "data": {"metadata": {"currentSeason": 43}, "segments": [
        {"stats": {"rankPoints": {"value": 4000}, "kdRatio": {"value": 1.05},
                   "matchesPlayed": {"value": 200}, "matchesWon": {"value": 100},
                   "matchesLost": {"value": 99}},
         "metadata": {"shortName": "Y11S2", "seasonName": "Old"},
         "attributes": {"season": 42, "gamemode": "pvp_ranked"}},
        {"stats": {"rankPoints": {"value": 4347}, "kdRatio": {"value": 1.08},
                   "matchesPlayed": {"value": 220}, "matchesWon": {"value": 112},
                   "matchesLost": {"value": 107}, "timePlayed": {"value": 1331994718}},
         "metadata": {"shortName": "Y11S3", "seasonName": "Split Fire"},
         "attributes": {"season": 43, "gamemode": "pvp_ranked"}},
    ]},
    "operators": [
        {"operator": "Pulse", "kd": 1.39, "winPercent": 55.03, "roundsPlayed": 2348},
        {"operator": "Jager", "kd": 1.10, "winPercent": 52.0, "roundsPlayed": 900},
    ],
    "totalsHoursPlayed": {"hours": 5706},
}}

calls = {"n": 0}


def fake_get(path, **params):
    calls["n"] += 1
    if path == "/ranks":
        return RANKS
    if path == "/v2/profile":
        return PROFILE
    if path == "/v2/fullstats":
        return FULL
    raise AssertionError(f"未预期的路径 {path}")


def boom_get(path, **params):
    raise OSError("网络挂了")


r6stats._get = fake_get
r6stats._cache.clear()
r6stats._ranks_cache = None

check("平台归一 pc→uplay", r6stats.normalize_platform("PC") == "uplay")
check("平台归一 ps5→psn", r6stats.normalize_platform("ps5") == "psn")
check("平台归一 xb→xbl", r6stats.normalize_platform("xb") == "xbl")
check("未知平台→None", r6stats.normalize_platform("switch") is None)

check("段位映射 4347→Diamond 2", r6stats.rank_name(4347) == "Diamond 2")
check("段位映射 0→Unranked", r6stats.rank_name(0) == "Unranked")

os.environ.pop("QQ_BOT_R6_KEY", None)
check("未配置 key → query 返回 None", r6stats.query("uplay", "MacieJay") is None)

os.environ["QQ_BOT_R6_KEY"] = "test-key"
os.environ["QQ_BOT_R6_TIMEOUT"] = "5"
check("已启用", r6stats.enabled() is True)

q = r6stats.query("pc", "MacieJay")
check("查询返回结果", isinstance(q, dict))
check("解析段位/RP", q and q["rank"] == "Diamond 2" and q["rp"] == 4347)
check("解析最高 RP", q and q["max_rp"] == 4442)
check("解析等级/总时长", q and q["level"] == 3512 and q["total_hours"] == 5706)
check("本赛季取最新（Y11S3）", q and q["season"]["code"] == "Y11S3")
check("本赛季胜负/胜率", q and q["season"]["wins"] == 112 and q["season"]["losses"] == 107
      and abs(q["season"]["win_rate"] - 50.9) < 1)
check("历史赛季不含本赛季", q and all(s["code"] != "Y11S3" for s in q["seasons"]))
check("干员按回合排序", q and q["operators"][0]["name"] == "Pulse")

text = r6stats.format_reply(q)
check("排版含昵称/段位", "MacieJay" in text and "Diamond 2" in text)
check("排版含本赛季 KD", "Y11S3" in text and "KD 1.08" in text)
check("排版含常用干员", "Pulse" in text)

before = calls["n"]
r6stats.query("pc", "MacieJay")
check("5 分钟内命中缓存不重复请求", calls["n"] == before, f"{before}->{calls['n']}")

r6stats._get = boom_get
r6stats._cache.clear()
check("网络失败 → None", r6stats.query("uplay", "Anyone") is None)

print(f"\n{'=' * 50}")
print(f"失败 {failures} 项")
sys.exit(1 if failures else 0)
