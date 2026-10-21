"""今日运势：确定性、四维/等级一致、不同人不同、排版完整。"""
import datetime as dt
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fortune  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


DAY = dt.date(2026, 10, 21)
a = fortune.daily("u1", DAY)
b = fortune.daily("u1", DAY)
check("同一天同一人结果固定", a == b)

r_next = fortune.daily("u1", DAY + dt.timedelta(days=1))
check("换一天结果会变", r_next != a)

dims = a["dims"]
check("四维各 0~2", all(0 <= v <= 2 for v in dims.values()), str(dims))
check("等级 = 四维之和 且落在 0~8",
      a["level"] == sum(dims.values()) and 0 <= a["level"] <= 8, str(a["level"]))
check("标题与签文完整", bool(a["title"]) and len(a["verse"].split("\n")) == 2)

levels = {fortune.daily(f"user{i}", DAY)["level"] for i in range(300)}
check("随机 300 人能覆盖 0~8 全档", levels == set(range(9)), str(sorted(levels)))

other = fortune.daily("u2", DAY)
check("不同用户结果不同", other["verse"] != a["verse"] or other["level"] != a["level"])

text = fortune.format_reply("u1", DAY)
for kw in ("【报告】", a["title"], "财运", "姻缘", "事业", "人品", "仅供娱乐"):
    check(f"排版含「{kw}」", kw in text)
check("黄历：装了 lunar-python 就该有【黄历】块",
      ("【黄历】" in text) or not a["almanac"])

print("\n---- 样例 ----")
print(text)

print(f"\n{'=' * 50}")
print(f"失败 {failures} 项")
sys.exit(1 if failures else 0)
