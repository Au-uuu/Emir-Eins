"""管理员汇报（notices）：key 过期判定、去重、私聊发送、检查项调度。

零依赖、不联网：admin/api 用假实现，去重用临时 push.db。
"""
import asyncio
import datetime
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import notices  # noqa: E402
from push_store import PushStore  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


class FakeApi:
    def __init__(self):
        self.sent = []

    async def post_c2c_message(self, **kw):
        self.sent.append(kw)


class FakeStore:
    def __init__(self, admins):
        self._a = admins

    async def list_admins(self):
        return self._a


async def main() -> int:
    tmp = tempfile.mkdtemp()

    # --- push_store 通知去重 ---
    ps = PushStore(os.path.join(tmp, "push.db"))
    check("新 key 未见过", await ps.unseen_notices(["a", "b"]) == ["a", "b"])
    await ps.mark_notices(["a"])
    check("已发 key 被过滤", await ps.unseen_notices(["a", "b"]) == ["b"])

    # --- check_r6_key ---
    today = datetime.date.today()
    os.environ.pop("QQ_BOT_R6_KEY", None)
    os.environ.pop("QQ_BOT_R6_KEY_EXPIRE", None)
    check("无 key → None", notices.check_r6_key() is None)
    os.environ["QQ_BOT_R6_KEY"] = "x"
    check("有 key 无到期日 → None", notices.check_r6_key() is None)
    os.environ["QQ_BOT_R6_KEY_EXPIRE"] = (today + datetime.timedelta(days=60)).strftime("%Y-%m-%d")
    check("还早 → None", notices.check_r6_key() is None)
    soon = (today + datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    os.environ["QQ_BOT_R6_KEY_EXPIRE"] = soon
    got = notices.check_r6_key()
    check("临近过期 → 有通知且含日期", bool(got) and got[0].startswith("r6key-soon") and soon in got[1])
    past = (today - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    os.environ["QQ_BOT_R6_KEY_EXPIRE"] = past
    got = notices.check_r6_key()
    check("已过期 → expired", bool(got) and got[0].startswith("r6key-expired"))
    os.environ["QQ_BOT_R6_KEY_EXPIRE"] = "not-a-date"
    check("日期格式错 → None（不炸）", notices.check_r6_key() is None)

    # --- notify_admins ---
    api = FakeApi()
    store = FakeStore(["u1", "u2"])
    n = await notices.notify_admins(api, store, ps, "hello", "k1")
    check("发给全部管理员", n == 2 and len(api.sent) == 2, str(len(api.sent)))
    n2 = await notices.notify_admins(api, store, ps, "hello", "k1")
    check("同一 key 不重发", n2 == 0 and len(api.sent) == 2, str(len(api.sent)))

    empty = FakeStore([])
    n3 = await notices.notify_admins(api, empty, ps, "hi", "k-noadmin")
    check("无管理员 → 0 且不标记已发", n3 == 0)
    check("无管理员时 key 仍可见", await ps.unseen_notices(["k-noadmin"]) == ["k-noadmin"])

    # --- check_once 调度 ---
    notices.CHECKS = [lambda: ("k2", "text2"), lambda: None]
    await notices.check_once(api, store, ps)
    check("check_once 对新通知发私聊", len(api.sent) == 4, str(len(api.sent)))
    await notices.check_once(api, store, ps)
    check("check_once 二次不重发", len(api.sent) == 4, str(len(api.sent)))

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
