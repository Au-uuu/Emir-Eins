# -*- coding: utf-8 -*-
"""用量账本测试：记录、汇总、分项。"""

import asyncio
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_usage_")
os.environ["QQ_BOT_USAGE"] = "1"
os.environ["QQ_BOT_USAGE_DB"] = os.path.join(TMP, "usage.db")

import usage_store  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


async def main() -> int:
    store = usage_store.UsageStore(db_path=os.path.join(TMP, "usage.db"))
    store.record("group:G1", "deepseek-v4.1-flash", "text", {
        "prompt_tokens": 100, "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 20},
        "opencode": {"list_cost": 0.001, "tokens": {"reasoning": 10}},
    })
    store.record("group:G1", "deepseek-v4.1-flash", "vision", {
        "prompt_tokens": 200, "completion_tokens": 80,
        "opencode": {"list_cost": 0.002, "tokens": {"reasoning": 30}},
    })
    store.record("memory", "deepseek-v4.1-flash", "learn", {
        "prompt_tokens": 300, "completion_tokens": 100,
        "opencode": {"list_cost": 0.003, "tokens": {"reasoning": 50}},
    })

    data = await store.summary(1)
    total = data["total"]
    check("调用 3 次", total["calls"] == 3, total)
    check("输入合计", total["prompt"] == 600, total)
    check("输出合计", total["completion"] == 230, total)
    check("推理合计", total["reasoning"] == 90, total)
    check("缓存合计", total["cached"] == 20, total)
    check("费用合计", abs(total["cost"] - 0.006) < 1e-9, total)
    kinds = {k["kind"]: k["calls"] for k in data["kinds"]}
    check("分项 text/vision/learn", kinds.get("text") == 1 and kinds.get("vision") == 1 and kinds.get("learn") == 1, kinds)
    scopes = {s["scope"]: s["calls"] for s in data["scopes"]}
    check("按群汇总", scopes.get("group:G1") == 2, scopes)

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
