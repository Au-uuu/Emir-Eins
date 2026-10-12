"""R6 绑定存储：绑定 / 查询 / 覆盖 / 解绑。零依赖，用临时库。"""
import asyncio
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6_bind import R6BindStore  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


async def main() -> int:
    tmp = tempfile.mkdtemp()
    s = R6BindStore(os.path.join(tmp, "r6bind.db"))

    check("未绑定返回 None", await s.get("u1") is None)
    check("空 openid 返回 None", await s.get("") is None)

    await s.bind("u1", "uplay", "MacieJay", "pid-1")
    g = await s.get("u1")
    check("绑定后可查", bool(g) and g["platform"] == "uplay"
          and g["name"] == "MacieJay" and g["profile_id"] == "pid-1")

    await s.bind("u1", "psn", "Other", None)
    g = await s.get("u1")
    check("重复绑定覆盖", bool(g) and g["platform"] == "psn" and g["name"] == "Other"
          and g["profile_id"] is None)

    check("解绑返回 True", await s.unbind("u1") is True)
    check("解绑后查不到", await s.get("u1") is None)
    check("解绑不存在的返回 False", await s.unbind("nobody") is False)

    # 不同 openid 互不影响
    await s.bind("a", "uplay", "A", None)
    await s.bind("b", "xbl", "B", None)
    check("多用户互不影响",
          (await s.get("a"))["name"] == "A" and (await s.get("b"))["name"] == "B")

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
