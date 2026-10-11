"""标签译名词库：查表命中、缺库降级、空值处理。

不依赖网络与第三方包（用标准库 sqlite3 造临时库）。
"""
import os
import sqlite3
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import names  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


# 1) 缺库：静默降级
os.environ["QQ_BOT_TAG_ZH_DB"] = os.path.join(tempfile.gettempdir(), "no_such_tag_zh.sqlite")
names._conn = None
names._failed = False
check("缺库时返回 None", names.cn_name("curren_chan") is None)

# 2) 造一个库
fd, path = tempfile.mkstemp(suffix=".sqlite")
conn = sqlite3.connect(path)
conn.execute(
    "CREATE TABLE tags(name TEXT PRIMARY KEY, category INT, cn_name TEXT, post_count INT)"
)
for row in [
    ("curren_chan_(umamusume)", 4, "真机伶（赛马娘）", 100),
    ("umamusume", 3, "赛马娘", 100),
    ("empty_cn", 4, "   ", 1),
    ("null_cn", 4, None, 1),
]:
    conn.execute("INSERT INTO tags VALUES(?,?,?,?)", row)
conn.commit()
conn.close()

os.environ["QQ_BOT_TAG_ZH_DB"] = path
names._conn = None
names._failed = False
check("角色查表", names.cn_name("curren_chan_(umamusume)") == "真机伶（赛马娘）")
check("作品查表", names.cn_name("umamusume") == "赛马娘")
check("空白 cn_name → None", names.cn_name("empty_cn") is None)
check("NULL cn_name → None", names.cn_name("null_cn") is None)
check("未收录 → None", names.cn_name("no_such_tag") is None)
check("空标签 → None", names.cn_name("") is None)
# Windows 上 sqlite 连接没关时删不掉文件
if names._conn is not None:
    names._conn.close()
    names._conn = None
try:
    os.remove(path)
except OSError:
    pass

print(f"\n{'=' * 50}")
print(f"失败 {failures} 项")
sys.exit(1 if failures else 0)
