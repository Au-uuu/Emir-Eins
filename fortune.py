"""今日运势：本地黄历（lunar-python）+ 确定性抽签。

设计要点：
- **同一个人同一天结果固定**（用 `sha256(日期:openid)` 当种子）——重发不会"刷好运"，
  也省得群里有人连点十次比大小。
- **不依赖任何外部 API**：黄历用 lunar-python 本地算（农历/干支/宜忌/冲煞/值神），
  运势本身没有权威数据源，用种子抽签即可（四维各 0~2，加起来定 8 级）。
- 语气走人格的【报告】体；结尾带"仅供娱乐"。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging

log = logging.getLogger("qqbot.fortune")

# 8 级（0 最差，8 最好）：标题 / 点评 / 签文（每条两句七言，不追求古奥，只求像回事）
_LEVELS = [
    {
        "title": "否极（否极泰来）",
        "comment": "否极泰来。今天就别做重大决定了，先把觉睡够。",
        "verses": [
            "乌云压顶雨将至，大事缓决病速医\n幸有东北贵人至，一伞遮身过急时",
            "否极终须有泰来，枯枝也会遇春开\n今朝且把心放宽，明日东风自入怀",
        ],
    },
    {
        "title": "困顿（宜静不宜动）",
        "comment": "宜静不宜动，少说多看。急事也拆成三步走。",
        "verses": [
            "寒鸦数点立枯枝，事不称心且自持\n闭户读书休问卜，春风自有到来时",
            "逆水行舟桨未停，一身疲色两肩风\n莫向人前说辛苦，山高路远见真功",
        ],
    },
    {
        "title": "蹇滞（诸事迟滞）",
        "comment": "诸事迟滞，能拖到明日的就别硬来。",
        "verses": [
            "雾锁重楼望不真，欲行还止费精神\n且收心火藏锋刃，静待云开见月轮",
            "棋到中盘势已迟，莫贪一子误全局\n退守三分非是弱，来日方长自有期",
        ],
    },
    {
        "title": "微澜（小有波折）",
        "comment": "小有波折，话到嘴边留半句。",
        "verses": [
            "风起微澜皱一池，事逢岔路费寻思\n且将急语咽三分，晚照归途未算迟",
            "小石拦路碍行车，绕道而行亦到家\n莫为闲言添烦恼，明朝依旧看朝霞",
        ],
    },
    {
        "title": "平宁（中平）",
        "comment": "中平之象，按部就班即可，不必求变。",
        "verses": [
            "云在青天水在瓶，寻常日子最安宁\n不求大吉无小咎，守得心平是太平",
            "半晴半雨半开怀，事到临头自有裁\n若问前程何所似，一江春水向东来",
        ],
    },
    {
        "title": "顺遂（吉）",
        "comment": "大体顺遂，注意别把好事说太满。",
        "verses": [
            "春风拂柳绿初匀，小径通幽见故人\n所求未必需急切，缓行一步自然真",
            "月映清池影自明，事逢难处有人应\n莫道今朝无所获，回头已在半山亭",
        ],
    },
    {
        "title": "通泰（上上大吉）",
        "comment": "顺风顺水，可以试着多走半步。",
        "verses": [
            "青龙盘柱气自华，文思泉涌笔生花\n旧事重提逢知己，一杯淡酒话桑麻",
            "玉堂照命路初开，西南有客送福来\n莫嫌起步三分慢，行到中流自快哉",
        ],
    },
    {
        "title": "鼎盛（巅峰鸿运）",
        "comment": "状态在线，适合推进拖了很久的事。",
        "verses": [
            "天乙贵人万事成，寅祭未提获重金\n双鱼跃门宜进取，所求皆得称人心",
            "将星坐镇展峥嵘，青绿加身向远峰\n戌时捷报从东至，竞技场上定输赢",
        ],
    },
    {
        "title": "太和（终极祥瑞）",
        "comment": "诸事皆宜，趁势而行；今天说的话容易被人记住。",
        "verses": [
            "紫微开天门献瑞，三奇六合共相随\n功名正当九天月，鸾凤和鸣非梦哉",
            "河图洛书天降财，冷门翻倍笑颜开\n红鸾星动良缘至，地涌甘泉金玉来",
        ],
    },
]

_DIMS = ("财运", "姻缘", "事业", "人品")


def _almanac(day: _dt.date) -> dict:
    """本地黄历；lunar-python 没装/出错就返回空 dict（不影响运势本体）。"""
    try:
        from lunar_python import Solar
    except Exception as exc:  # noqa: BLE001
        log.info("[运势] lunar-python 不可用：%s", exc)
        return {}
    try:
        lunar = Solar.fromYmd(day.year, day.month, day.day).getLunar()
        return {
            "lunar": f"{lunar.getYearInGanZhi()}年 {lunar.getMonthInChinese()}月{lunar.getDayInChinese()}",
            "ganzhi": " ".join((lunar.getYearInGanZhi(), lunar.getMonthInGanZhi(), lunar.getDayInGanZhi())),
            "yi": (lunar.getDayYi() or [])[:5],
            "ji": (lunar.getDayJi() or [])[:5],
            "chong": lunar.getDayChongDesc(),
            "sha": lunar.getDaySha(),
            "zhishen": lunar.getDayTianShen(),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("[运势] 黄历计算失败：%s", exc)
        return {}


def daily(openid: str, day: _dt.date | None = None) -> dict:
    """算某人某天的运势（同一天同一个人结果固定）。"""
    day = day or _dt.date.today()
    seed = hashlib.sha256(f"{day.isoformat()}:{openid}".encode("utf-8")).digest()
    # 四维各 0~2 → 等级 0~8，分布中间高两头低（看着比均匀随机自然）
    dims = [seed[0] % 3, seed[1] % 3, seed[2] % 3, seed[3] % 3]
    level = sum(dims)
    info = _LEVELS[level]
    verse = info["verses"][seed[4] % len(info["verses"])]
    almanac = _almanac(day)
    return {
        "date": day,
        "level": level,
        "title": info["title"],
        "comment": info["comment"],
        "verse": verse,
        "dims": dict(zip(_DIMS, dims)),
        "almanac": almanac,
    }


def _stars(n: int) -> str:
    n = max(0, min(8, n))
    return "★" * n + "☆" * (8 - n)


def _dim_stars(v: int) -> str:
    return "★" * v + "☆" * (2 - v)


def format_reply(openid: str, day: _dt.date | None = None) -> str:
    """排版成一条可读的文本回复（人格的【报告】体）。"""
    r = daily(openid, day)
    d = r["date"]
    a = r["almanac"]
    lines = [
        "【报告】今日运势已解析——主人这一日，本机算过了。",
        f"{d.isoformat()}" + (f" · 农历{a['lunar']}" if a else ""),
    ]
    if a and a.get("ganzhi"):
        lines.append(f"干支　{a['ganzhi']}")
    lines.append("")
    lines.append(f"{r['title']}　{_stars(r['level'])}")
    v1, v2 = (r["verse"].split("\n") + ["", ""])[:2]
    lines.append(v1.strip())
    if v2.strip():
        lines.append(v2.strip())
    lines.append("")
    lines.append("　".join(f"{k} {_dim_stars(v)}" for k, v in r["dims"].items()))
    if a:
        lines.append("")
        lines.append("【黄历】宜：" + "、".join(a.get("yi") or ["—"]))
        lines.append("　　　　忌：" + "、".join(a.get("ji") or ["—"]))
        lines.append(f"　　　　冲{a.get('chong') or '—'}　煞{a.get('sha') or '—'}　值神{a.get('zhishen') or '—'}")
    lines.append("")
    lines.append(f"【提示】{r['comment']}")
    lines.append("（仅供娱乐，本机只负责算，不负责灵）")
    return "\n".join(lines)
