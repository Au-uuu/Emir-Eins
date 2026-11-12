# -*- coding: utf-8 -*-
"""每日用量日报测试（不联网、不发群）。"""

import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_usage_report_")
os.environ["QQ_BOT_USAGE"] = "1"
os.environ["QQ_BOT_USAGE_DB"] = os.path.join(TMP, "usage.db")
os.environ["QQ_BOT_USAGE_REPORT"] = "1"
os.environ["QQ_BOT_USAGE_REPORT_HOUR"] = "9"
os.environ["QQ_BOT_ADMIN_GROUP"] = "GADMIN"
os.environ["QQ_BOT_USAGE_REPORT_STATE"] = os.path.join(TMP, "state.json")
os.environ["QQ_BOT_USAGE_REPORT_TOP_CNY"] = "0.01"

import usage_report  # noqa: E402
import usage_store  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


class FakeApi:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    async def post_group_message(self, group_openid, content):
        if self.error:
            raise RuntimeError(self.error)
        self.sent.append((group_openid, content))
        return {"id": "ok"}

class FakeMessage:
    def __init__(self, group="GADMIN"):
        self.group_openid = group
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return {"id": "sent"}


async def main() -> int:
    store = usage_store.UsageStore(db_path=os.path.join(TMP, "usage.db"))
    day = "2026-10-28"
    start, _ = usage_report._day_bounds(day)
    rows = [
        (start + 3600, "group:A", "deepseek", "text", 100, 20, 5, 10, 0.010),
        (start + 7200, "c2c:B", "deepseek", "vision", 200, 30, 0, 0, 0.020),
        (start + 10800, "memory", "deepseek", "learn", 50, 10, 0, 0, 0.005),
        # 前一天的数据不应计入
        (start - 60, "group:A", "deepseek", "text", 999, 999, 0, 0, 9.0),
    ]
    with store._connect() as conn:
        for r in rows:
            conn.execute(
                "INSERT INTO usage(ts, scope, model, kind, prompt, completion, "
                "reasoning, cached, cost) VALUES (?,?,?,?,?,?,?,?,?)",
                r,
            )

    print("[1] 日报内容")
    text = await usage_report.build_report(store, day)
    check("标题是当天", "2026-10-28" in text, text)
    check("调用 3 次", "调用 3 次" in text, text)
    check("输入 350", "输入 350" in text, text)
    check("输出 60", "输出 60" in text, text)
    check("费用合计", "$0.0350" in text, text)
    check("包含类型", "对话 1" in text and "看图 1" in text and "学习 1" in text, text)
    check("包含高用量群", "高用量群" in text and "群 A" in text, text)
    check("不显示单聊/低额", "单聊 B" not in text and "memory" not in text, text)

    os.environ["QQ_BOT_USAGE_REPORT_TOP_CNY"] = "0.5"
    text_high = await usage_report.build_report(store, day)
    check("默认 0.5 元以下不显示 Top", "高用量群" not in text_high, text_high)
    os.environ["QQ_BOT_USAGE_REPORT_TOP_CNY"] = "0.01"

    class NamedHttp:
        async def request(self, route):
            return {"group_name": "测试群"}

    class NamedApi(FakeApi):
        _http = NamedHttp()

    usage_report._GROUP_NAMES_PATH = os.path.join(TMP, "group_names.json")
    text_named = await usage_report.build_report(store, day, api=NamedApi())
    check("能显示群名字", "测试群" in text_named, text_named)

    print("[2] 到点发送 + 当天去重")
    api = FakeApi()
    sent = await usage_report.maybe_send(
        api, store, now=datetime(2026, 10, 29, 10, 0)
    )
    check("10 点会发", sent is True, sent)
    check("只发一条", len(api.sent) == 1, api.sent)
    check("发到配置的群", api.sent and api.sent[0][0] == "GADMIN", api.sent)
    check("发的是日报", api.sent and "【用量日报】2026-10-28" in api.sent[0][1], api.sent)
    sent2 = await usage_report.maybe_send(
        api, store, now=datetime(2026, 10, 29, 11, 0)
    )
    check("同一天不重复", sent2 is False and len(api.sent) == 1, api.sent)

    print("[3] 未到点不发")
    os.environ["QQ_BOT_USAGE_REPORT_STATE"] = os.path.join(TMP, "state2.json")
    api2 = FakeApi()
    sent = await usage_report.maybe_send(
        api2, store, now=datetime(2026, 10, 30, 8, 0)
    )
    check("8 点不发", sent is False and not api2.sent, api2.sent)

    print("[4] 没有配置群不发")
    os.environ["QQ_BOT_ADMIN_GROUP"] = ""
    os.environ["QQ_BOT_USAGE_REPORT_GROUP"] = ""
    api3 = FakeApi()
    check(
        "无群配置直接跳过",
        await usage_report.maybe_send(api3, store, now=datetime(2026, 10, 30, 10, 0))
        is False,
    )
    os.environ["QQ_BOT_ADMIN_GROUP"] = "GADMIN"

    print("[5] 22009 配额失败不重试")
    os.environ["QQ_BOT_USAGE_REPORT_STATE"] = os.path.join(TMP, "state3.json")
    bad = FakeApi(error="22009 msg limit")
    check(
        "失败返回 False",
        await usage_report.maybe_send(bad, store, now=datetime(2026, 10, 30, 10, 0))
        is False,
    )
    check(
        "失败后当天不重试",
        await usage_report.maybe_send(bad, store, now=datetime(2026, 10, 30, 11, 0))
        is False,
    )

    print("[6] 主动发送失败后被动补发")
    wrong = FakeMessage("OTHER")
    check("非管理员群不补发", await usage_report.flush_pending(wrong, 1) is False)
    msg = FakeMessage("GADMIN")
    ok = await usage_report.flush_pending(msg, 7)
    check("补发成功", ok is True and len(msg.replies) == 1, msg.replies)
    check("补发带 msg_seq", msg.replies and msg.replies[0].get("msg_seq") == 7, msg.replies)
    check("补发内容是日报", msg.replies and "用量日报" in msg.replies[0].get("content", ""), msg.replies)
    state = json.load(open(os.environ["QQ_BOT_USAGE_REPORT_STATE"], encoding="utf-8"))
    check("pending 已清", "pending" not in state, state)
    check("记录为已发", state.get("last_sent") is not None, state)

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
