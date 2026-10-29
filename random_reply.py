# -*- coding: utf-8 -*-
"""群聊随机接话：按群调整概率（任何人都能改，仅影响本群）。

命令：
  /接话                 查看本群当前概率
  /接话 5%              设成本群 5%
  /接话 0.03            设成本群 3%
  /接话 关              本群关闭
  /接话 默认            删除本群设置，恢复 .env 的全局概率

设置存 memory.db 的 group_settings 表；进程内另有缓存，避免每条群消息都查库。
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("qqbot.random_reply")

SETTING_KEY = "random_reply_p"
# 上限 50%：再高就是刷屏；冷却仍由 QQ_BOT_RANDOM_REPLY_COOLDOWN 控制
MAX_P = 0.5

PATTERN = re.compile(r"^/(?:\u63a5\u8bdd|\u968f\u673a\u63a5\u8bdd)(?:\s+(.*))?$", re.DOTALL)

# {group_openid: probability}；只存有群级覆盖的群
_overrides: dict[str, float] = {}


def probability(group_openid: str, default: float) -> float:
    """本群生效概率：有覆盖用覆盖，没有用全局默认。"""
    return _overrides.get(group_openid, default)


def _parse_p(raw: str, clamp: bool = True) -> float | None:
    """解析 5% / 0.05 / 5 三种写法；非法返回 None。"""
    text = (raw or "").strip().lower()
    if not text:
        return None
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    try:
        value = float(text)
    except ValueError:
        return None
    # 不带小数点的写法按百分数理解：`1` = 1%，`5` = 5%；
    # 带小数点仍是概率：`0.03` = 3%，`0.5` = 50%。
    bare_int = "." not in text
    if percent or value > 1 or (bare_int and value >= 1):
        value /= 100.0
    value = max(0.0, value)
    return min(MAX_P, value) if clamp else value


def _fmt(p: float) -> str:
    pct = p * 100.0
    if pct == int(pct):
        return f"{int(pct)}%"
    return f"{pct:.1f}%"


async def load(memory) -> None:
    """启动时从 memory.db 读回所有群级覆盖。"""
    _overrides.clear()
    try:
        rows = await memory.list_group_settings(SETTING_KEY)
    except Exception as exc:  # noqa: BLE001
        log.warning("[接话] 读取群设置失败：%s", exc)
        return
    for scope, value in rows.items():
        if not scope.startswith("group:"):
            continue
        p = _parse_p(value)
        if p is not None:
            _overrides[scope.split(":", 1)[1]] = p
    if _overrides:
        log.info("[接话] 已加载 %d 个群的随机接话覆盖", len(_overrides))


async def _set(memory, gid: str, p: float) -> bool:
    scope = f"group:{gid}"
    _overrides[gid] = p
    if memory.enabled():
        await memory.set_group_setting(scope, SETTING_KEY, str(p))
        return True
    return False


async def _reset(memory, gid: str) -> bool:
    scope = f"group:{gid}"
    _overrides.pop(gid, None)
    if memory.enabled():
        await memory.delete_group_setting(scope, SETTING_KEY)
        return True
    return False


async def handle(message, scope: str, text: str, default_p: float, memory) -> bool:
    """
    处理 /接话 指令。返回 True 表示已消费（调用方直接 return）。

    任何人都能改，不检查管理员：只影响当前群，且上限 50%，防止有人直接拉满刷屏。
    """
    match = PATTERN.match((text or "").strip())
    if not match:
        return False
    if not scope.startswith("group:"):
        await message.reply(content="这个指令只能在群里用；私聊本来就每条都会回。")
        return True

    gid = scope.split(":", 1)[1]
    arg = (match.group(1) or "").strip()
    lower = arg.lower()
    current = probability(gid, default_p)
    is_override = gid in _overrides

    if not arg or lower in ("状态", "查", "status"):
        source = "本群单独设置" if is_override else f"跟随全局默认 {_fmt(default_p)}"
        await message.reply(
            content=(
                f"本群随机接话：{_fmt(current)}（{source}）\n"
                f"调整：/接话 5%　/接话 关　/接话 默认"
            )
        )
        return True

    if lower in ("默认", "重置", "reset", "继承"):
        saved = await _reset(memory, gid)
        suffix = "" if saved else "\n（记忆未开启，仅本次运行有效）"
        await message.reply(
            content=f"已恢复全局默认：{_fmt(default_p)}{suffix}"
        )
        return True

    if lower in ("关", "关闭", "off", "停", "0"):
        saved = await _set(memory, gid, 0.0)
        suffix = "" if saved else "\n（记忆未开启，仅本次运行有效）"
        await message.reply(content=f"本群随机接话已关闭。{suffix}")
        return True

    raw_p = _parse_p(arg, clamp=False)
    if raw_p is None:
        await message.reply(
            content="写法：/接话 5%　/接话 0.03　/接话 关　/接话 默认"
        )
        return True
    p = min(raw_p, MAX_P)
    capped = (
        f"\n（最高 {_fmt(MAX_P)}，已按上限设置）" if raw_p > MAX_P else ""
    )
    saved = await _set(memory, gid, p)
    suffix = "" if saved else "\n（记忆未开启，仅本次运行有效）"
    await message.reply(
        content=f"本群随机接话已设为 {_fmt(p)}。{capped}{suffix}"
    )
    return True
