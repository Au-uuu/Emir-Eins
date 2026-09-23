"""
冒烟测试：验证 bot.py 能正常导入、读取凭据，并成功换取 access_token。

不建立 WebSocket 长连接，只做静态与鉴权检查，适合在正式启动前快速自检。

用法：python smoke_test.py
"""

import sys
import traceback

# Windows 控制台默认 GBK，统一按 UTF-8 输出并忽略无法编码的字符
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def main() -> int:
    print("[1/5] 导入 bot.py ...")
    try:
        import bot  # noqa: F401
    except SystemExit as exc:
        print(f"    [FAIL] 启动被中止（多半是缺少凭据）: {exc}")
        return 1
    except Exception:
        print("    [FAIL] 导入失败：")
        traceback.print_exc()
        return 1
    print("    [OK] 导入成功")

    print("[2/5] 检查凭据读取 ...")
    if not bot.APP_ID or not bot.APP_SECRET:
        print("    [FAIL] APP_ID / APP_SECRET 为空")
        return 1
    print(f"    [OK] APP_ID={bot.APP_ID}，SECRET 长度={len(bot.APP_SECRET)}")

    print("[3/5] 检查事件处理器 ...")
    handlers = [
        "on_ready",
        "on_group_at_message_create",
        "on_group_message_create",
        "on_c2c_message_create",
    ]
    missing = [h for h in handlers if not hasattr(bot.MyClient, h)]
    if missing:
        print(f"    [FAIL] 缺少处理器: {missing}")
        return 1
    print(f"    [OK] {len(handlers)} 个处理器齐备")

    print("[4/5] 检查图片库与上传模块 ...")
    try:
        import image_store  # noqa: F401
        import uploader  # noqa: F401
    except Exception:
        print("    [FAIL] 模块导入失败：")
        traceback.print_exc()
        return 1

    import os

    for path in (bot.store.db_path, bot.store.images_dir):
        parent = path if os.path.isdir(path) else os.path.dirname(path)
        if not os.path.isdir(parent):
            print(f"    [FAIL] 目录不存在: {parent}")
            return 1
    stats = None
    try:
        import asyncio

        stats = asyncio.run(bot.store.stats())
    except Exception:
        print("    [FAIL] 读取图片库失败：")
        traceback.print_exc()
        return 1
    print(
        f"    [OK] 图片库可用，现有 {stats['total']} 张图 / "
        f"{stats['keywords']} 个关键词"
    )

    print("[5/5] 调用开放平台换取 access_token ...")
    try:
        import json
        import urllib.request

        payload = json.dumps(
            {"appId": bot.APP_ID, "clientSecret": bot.APP_SECRET}
        ).encode()
        req = urllib.request.Request(
            "https://api.bot.qq.com/app/getAppAccessToken",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())

        if data.get("access_token"):
            print(f"    [OK] 鉴权成功，有效期 {data.get('expires_in')} 秒")
        else:
            print(
                f"    [FAIL] 鉴权失败: code={data.get('code')} "
                f"message={data.get('message')}"
            )
            return 1
    except Exception:
        print("    [FAIL] 请求异常：")
        traceback.print_exc()
        return 1

    print("\n全部通过，可以执行 python bot.py 启动机器人。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
