"""向 bot 管理员汇报（主动私聊）。

用途：**需要人来处理的事**自动私聊通知管理员。第一个检查项是「R6 API key 快过期」；
以后要加别的（额度、证书、服务异常…），往 `CHECKS` 里塞一个返回 `(key, text)` 的函数即可。

设计：
- 定时跑（默认每 12 小时）+ 启动时各跑一次；
- 每个通知有**唯一 key**，发过就不再发（持久化在 `data/push.db` 的 `seen_notices`）；
- 用 `api.post_c2c_message` 主动私聊每个管理员；失败只记日志（主动消息可能被平台限制）。

失败/无管理员一律不影响主流程（与 kb/websearch 同策略）。
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os

log = logging.getLogger("qqbot.notices")

DEFAULT_MINUTES = 720  # 12 小时
DEFAULT_WARN_DAYS = 7


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _minutes() -> float:
    try:
        return float(_env("QQ_BOT_NOTICE_MINUTES") or DEFAULT_MINUTES)
    except ValueError:
        return float(DEFAULT_MINUTES)


def _warn_days() -> int:
    try:
        return int(_env("QQ_BOT_NOTICE_WARN_DAYS") or DEFAULT_WARN_DAYS)
    except ValueError:
        return DEFAULT_WARN_DAYS


def check_r6_key() -> tuple[str, str] | None:
    """R6 API key 快过期/已过期时返回 (key, 通知正文)，否则 None。

    过期日期由 `QQ_BOT_R6_KEY_EXPIRE`（YYYY-MM-DD）配置——API 本身不返回到期日。
    """
    if not _env("QQ_BOT_R6_KEY"):
        return None
    expire = _env("QQ_BOT_R6_KEY_EXPIRE")
    if not expire:
        return None
    try:
        d = datetime.datetime.strptime(expire, "%Y-%m-%d").date()
    except ValueError:
        log.warning("[通知] QQ_BOT_R6_KEY_EXPIRE 格式不对（应 YYYY-MM-DD）：%s", expire)
        return None
    days = (d - datetime.date.today()).days
    if days < 0:
        return (
            f"r6key-expired:{expire}",
            f"【机凯种·报告】R6 战绩查询用的 key 已于 {expire} 过期（{-days} 天前）。"
            f"请到 r6.arenyze.com 重新生成，更新服务器 .env 的 QQ_BOT_R6_KEY 后重启。",
        )
    if days <= _warn_days():
        return (
            f"r6key-soon:{expire}",
            f"【机凯种·报告】R6 战绩查询用的 key 将于 {expire} 过期（还剩 {days} 天）。"
            f"请到 r6.arenyze.com 重新生成，更新服务器 .env 的 QQ_BOT_R6_KEY 后重启。",
        )
    return None


# 检查项清单：每个是无参函数，返回 (唯一key, 通知正文) 或 None。加新检查往这里塞。
CHECKS = [check_r6_key]


async def notify_admins(api, store, pstore, text: str, key: str) -> int:
    """给所有管理员私聊一条通知；按 key 去重。返回成功发送人数。"""
    if not await pstore.unseen_notices([key]):
        return 0
    admins = await store.list_admins()
    if not admins:
        log.info("[通知] 尚无管理员可通知，跳过 %s", key)
        return 0
    sent = 0
    for oid in admins:
        try:
            await api.post_c2c_message(openid=oid, content=text)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("[通知] 私聊管理员 %s… 失败：%s", (oid or "")[:8], exc)
    if sent:
        await pstore.mark_notices([key])
        log.info("[通知] 已通知 %d 名管理员：%s", sent, key)
    return sent


async def check_once(api, store, pstore) -> None:
    """跑一遍所有检查项，有结果就通知管理员。"""
    for fn in CHECKS:
        try:
            got = fn()
        except Exception:  # noqa: BLE001
            log.exception("[通知] 检查项 %s 异常", getattr(fn, "__name__", fn))
            continue
        if not got:
            continue
        key, text = got
        try:
            await notify_admins(api, store, pstore, text, key)
        except Exception:  # noqa: BLE001
            log.exception("[通知] 通知管理员失败：%s", key)


async def poll_loop(api, store, pstore) -> None:
    """常驻轮询：bot.py 在 on_ready 里 create_task 启动。间隔 0 = 关闭。"""
    minutes = _minutes()
    if minutes <= 0:
        log.info("[通知] 管理员汇报未启用（QQ_BOT_NOTICE_MINUTES=0）")
        return
    log.info(
        "[通知] 管理员汇报已启动：间隔 %.0f 分钟，检查项=%s",
        minutes, [getattr(f, "__name__", str(f)) for f in CHECKS],
    )
    while True:
        try:
            await check_once(api, store, pstore)
        except Exception:  # noqa: BLE001
            log.exception("[通知] 本轮检查异常")
        await asyncio.sleep(minutes * 60)
