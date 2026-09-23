"""验证看门狗：安装、活跃刷新、超时退出。"""

import asyncio
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\DSH\qqbot")

import watchdog  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


async def main() -> int:
    print("[1] 安装看门狗")
    watchdog.install()
    check("install() 成功", watchdog._installed)
    watchdog.install()
    check("重复 install() 安全", True)

    print("\n[2] 活跃时间刷新")
    idle0, _ = watchdog.last_activity()
    # 模块导入会耗时，这里只要求计时器是有限值且在合理范围
    check("初始计时正常", 0 <= idle0 < 30, f"{idle0:.2f}s")
    await asyncio.sleep(0.2)
    idle1, _ = watchdog.last_activity()
    check("未活动时计时增长", idle1 > idle0, f"{idle1:.2f}s")
    watchdog.touch("TEST_EVENT")
    idle2, last = watchdog.last_activity()
    check("touch 后重置", idle2 < 0.1, f"{idle2:.2f}s")
    check("记录了事件名", last == "TEST_EVENT", last)

    print("\n[3] 超时退出行为（子进程实测）")
    code = r'''
import asyncio, os, sys
sys.path.insert(0, r"D:\DSH\qqbot")
import watchdog
async def main():
    # 0.5 秒空闲即判定失效
    await watchdog.watch(idle_timeout=0.5, check_interval=0.2)
asyncio.run(main())
'''
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )
    check("看门狗触发退出", proc.returncode == 2, f"exit={proc.returncode}")
    check("退出前打印了原因", "静默失效" in proc.stderr, proc.stderr.strip()[-90:])

    print("\n[4] 未超时不应退出")
    code2 = r'''
import asyncio, sys
sys.path.insert(0, r"D:\DSH\qqbot")
import watchdog
async def main():
    # 空闲阈值 10 秒，但只观察 1 秒，且中途有活动
    task = asyncio.create_task(watchdog.watch(idle_timeout=10, check_interval=0.2))
    for _ in range(5):
        await asyncio.sleep(0.2)
        watchdog.touch("PING")
    task.cancel()
    print("STILL_ALIVE")
asyncio.run(main())
'''
    proc2 = subprocess.run(
        [sys.executable, "-c", code2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )
    check("有活动时不退出", "STILL_ALIVE" in proc2.stdout and proc2.returncode == 0,
          f"exit={proc2.returncode}")

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
