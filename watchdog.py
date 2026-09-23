"""
连接看门狗。

问题：botpy 的 WebSocket 长连接可能**静默失效**——进程还活着、心跳循环也还在跑，
但平台的事件再也收不到，而且日志里没有任何报错。此时机器人看起来"在线"，
实际已经完全不响应，只能手动重启。

方案：记录最后一次收到网关消息的时间，一旦超过阈值没有收到任何东西，
就打印明确原因并让进程退出（exit code 2），交给进程守护/重启脚本拉起。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger("qqbot.watchdog")

_last_activity: float = time.monotonic()
_last_event: str = "(尚未收到任何事件)"
_installed = False


def touch(event: str = "") -> None:
    """收到任何网关消息时调用。"""
    global _last_activity, _last_event
    _last_activity = time.monotonic()
    if event:
        _last_event = event


def last_activity() -> tuple[float, str]:
    """返回 (距上次活动的秒数, 最后一个事件名)。"""
    return time.monotonic() - _last_activity, _last_event


def install() -> None:
    """
    猴补丁网关的消息入口，任何下行消息都会刷新活跃时间。
    安装在最靠近 socket 的一层，确保"收到东西"就能被记录。
    """
    global _installed
    if _installed:
        return

    from botpy.gateway import BotWebSocket

    original = BotWebSocket.on_message

    async def patched(self, ws, message):
        # 从原始 JSON 里取事件名，失败也不影响主流程
        try:
            import json

            name = json.loads(message).get("t") or ""
            if name not in ("READY", "RESUMED"):
                touch(name)
        except Exception:  # noqa: BLE001
            touch()
        return await original(self, ws, message)

    BotWebSocket.on_message = patched
    _installed = True
    log.info("连接看门狗已安装")


async def watch(
    idle_timeout: float = 900.0, check_interval: float = 30.0
) -> None:
    """
    监控连接活跃度。

    idle_timeout 默认 15 分钟：超过这个时间没收到任何网关消息，就认为连接已死。
    正常有群消息时不会触发；安静群聊里可以按需调大。
    """
    log.info(
        "看门狗启动：超过 %.0f 分钟无任何网关消息则重启进程", idle_timeout / 60
    )
    while True:
        await asyncio.sleep(check_interval)
        idle, last = last_activity()
        if idle >= idle_timeout:
            log.error(
                "连接疑似静默失效：已 %.1f 分钟未收到任何网关消息"
                "（最后事件：%s），主动退出以便重启",
                idle / 60,
                last,
            )
            # 给日志留出刷盘时间，然后退出，交给守护脚本拉起
            logging.shutdown()
            os._exit(2)
        log.debug("看门狗：连接活跃，距上次消息 %.1f 秒", idle)
