"""本地知识库检索（BM25）：命中排序、无效路径降级、_ 开头文件不索引。

零依赖，用临时目录造语料即可测。
"""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


def reset() -> None:
    kb._loaded = False
    kb._chunks = []
    kb._df = {}
    kb._n = 0
    kb._avgdl = 0.0


d = tempfile.mkdtemp()
with open(os.path.join(d, "角色.md"), "w", encoding="utf-8") as fh:
    fh.write(
        "佩丽卡是《明日方舟：终末地》的角色，黑长发、赤色角、机械臂义体。\n\n"
        "阿米娅是《明日方舟》的看板娘，粉棕色头发、兔耳、穿制服。\n\n"
        "初音未来是 Vocaloid 的虚拟歌手，蓝绿色双马尾。\n"
    )
# 以 _ 开头的文件不应被索引
with open(os.path.join(d, "_说明.md"), "w", encoding="utf-8") as fh:
    fh.write("这个文件不该被检索：独特关键字 zzzqqq\n")

os.environ["QQ_BOT_KB_DIR"] = d
os.environ["QQ_BOT_KB_MIN_SCORE"] = "0.1"
reset()

hits = kb.search("佩丽卡是谁")
check("命中佩丽卡", bool(hits) and "佩丽卡" in hits[0][1], str(hits[:1]))

hits = kb.search("阿米娅兔耳")
check("命中阿米娅", bool(hits) and "阿米娅" in hits[0][1], str(hits[:1]))

ctx = kb.context("阿米娅")
check("context 带资料前缀且含内容", "本机知识库" in ctx and "阿米娅" in ctx, ctx[:40])

hits = kb.search("zzzqqq")
check("_ 开头文件不索引", not hits, str(hits))

reset()
os.environ["QQ_BOT_KB_MIN_SCORE"] = "99"
hits = kb.search("佩丽卡")
check("分数低于阈值 → 不返回", not hits, str(hits))

# 目录不存在 → 静默降级
reset()
os.environ["QQ_BOT_KB_MIN_SCORE"] = "0.1"
os.environ["QQ_BOT_KB_DIR"] = os.path.join(tempfile.gettempdir(), "no_such_kb_dir")
check("目录不存在 → 空结果", kb.context("佩丽卡") == "")

print(f"\n{'=' * 50}")
print(f"失败 {failures} 项")
sys.exit(1 if failures else 0)
