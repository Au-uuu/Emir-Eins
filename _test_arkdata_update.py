# -*- coding: utf-8 -*-
"""数值快照更新检查测试（不联网）。"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_arkupdate_")
os.environ["QQ_BOT_ADMIN_GROUP"] = "GADMIN"
os.environ["QQ_BOT_ARKDATA_CHECK"] = "1"
os.environ["QQ_BOT_ARKDATA_CHECK_HOUR"] = "12"

import arkdata_update  # noqa: E402

arkdata_update.STATE_PATH = os.path.join(TMP, "state.json")
arkdata_update.META_PATH = os.path.join(TMP, "meta.json")

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


class FakeApi:
    def __init__(self):
        self.sent = []

    async def post_group_message(self, group_openid, content):
        self.sent.append((group_openid, content))
        return {"id": "ok"}


def write_meta(sha):
    with open(arkdata_update.META_PATH, "w", encoding="utf-8") as fh:
        json.dump({"source_commit": sha, "operators": 1082}, fh)


def commits(sha):
    return {
        path: {"path": path, "sha": sha, "date": "2026-11-02T00:00:00Z", "message": "update"}
        for path in (
            "excel/character_table.json",
            "excel/skill_table.json",
            "excel/charword_table.json",
            "excel/handbook_info_table.json",
        )
    }


async def main() -> int:
    print("[1] 未到 12 点不查")
    api = FakeApi()
    check("11 点跳过", await arkdata_update.check_once(api, datetime(2026, 11, 2, 11), commits("new")) is False)
    check("未发送", not api.sent)

    print("[2] 有更新提醒")
    write_meta("old")
    api = FakeApi()
    sent = await arkdata_update.check_once(api, datetime(2026, 11, 2, 12), commits("new12345678"))
    check("返回已提醒", sent is True)
    check("发到管理员群", api.sent and api.sent[0][0] == "GADMIN", api.sent)
    check("消息含新旧 commit", "old" in api.sent[0][1] and "new12345678" in api.sent[0][1], api.sent[0][1] if api.sent else "")

    print("[3] 当天不重复")
    api2 = FakeApi()
    check("同日不再查", await arkdata_update.check_once(api2, datetime(2026, 11, 2, 13), commits("new12345678")) is False)
    check("没有重复发", not api2.sent)

    print("[4] 无更新")
    write_meta("same12345678")
    state = json.load(open(arkdata_update.STATE_PATH, encoding="utf-8"))
    state["last_checked"] = "2026-11-01"
    json.dump(state, open(arkdata_update.STATE_PATH, "w", encoding="utf-8"))
    api3 = FakeApi()
    check("相同 commit 不提醒", await arkdata_update.check_once(api3, datetime(2026, 11, 3, 12), commits("same12345678")) is False)
    check("无发送", not api3.sent)

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
