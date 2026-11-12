# -*- coding: utf-8 -*-
"""每日模型用量日报：每天固定时间发到管理员群。

配置：
  QQ_BOT_USAGE_REPORT=1/0           开关（默认开）
  QQ_BOT_USAGE_REPORT_HOUR=9        每天几点后发（0-23，默认 9）
  QQ_BOT_ADMIN_GROUP=<群 openid>    管理员群；也可用 QQ_BOT_USAGE_REPORT_GROUP
  QQ_BOT_USAGE_REPORT_STATE=...     状态文件（测试用，可选）

日报内容：调用次数、输入/输出/推理/缓存 token、美元花费（折算人民币）、
按类型和按会话的 Top。只统计前一天 00:00–24:00（服务器本地时区）。

设计取舍：
  - 用 `api.post_group_message` 主动发群消息，不依赖有人说话；
  - 状态文件记录 last_attempt，进程重启不会同一天重复发；
  - 发送失败（含 22009 主动消息配额）只记日志，不反复重试，避免刷屏。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta

log = logging.getLogger("qqbot.usage_report")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_PATH = os.path.join(DATA_DIR, "usage_report.json")
CHECK_SECONDS = 300.0

_KIND_NAMES = {
    "text": "对话",
    "vision": "看图",
    "learn": "学习",
    "summary": "摘要",
}

_CNY_PER_USD = 7.2
_GROUP_NAMES_PATH = os.path.join(DATA_DIR, "group_names.json")


def top_cny() -> float:
    """日报里只显示花费超过这个人民币金额的群；默认 ¥0.5。"""
    try:
        value = float(os.getenv("QQ_BOT_USAGE_REPORT_TOP_CNY", "0.5") or 0.5)
    except ValueError:
        value = 0.5
    return max(0.0, value)


def _load_group_names() -> dict:
    try:
        with open(_GROUP_NAMES_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_group_names(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_GROUP_NAMES_PATH) or ".", exist_ok=True)
        tmp = _GROUP_NAMES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _GROUP_NAMES_PATH)
    except OSError as exc:
        log.warning("[日报] 群名缓存写入失败：%s", exc)


async def _group_name(api, group_openid: str) -> str:
    """查群名（缓存到 data/group_names.json）；拿不到返回空串。"""
    if not group_openid:
        return ""
    cache = _load_group_names()
    if group_openid in cache:
        return str(cache[group_openid] or "")
    if api is None or not hasattr(api, "_http"):
        return ""
    try:
        from botpy.http import Route

        data = await api._http.request(
            Route(
                "GET",
                "/v2/groups/{group_openid}/info",
                group_openid=group_openid,
            )
        )
        name = str((data or {}).get("group_name") or "").strip()
        if name:
            cache[group_openid] = name
            _save_group_names(cache)
        return name
    except Exception as exc:  # noqa: BLE001
        log.warning("[日报] 查群名失败 %s：%s", group_openid[:8], exc)
        return ""


def enabled() -> bool:
    return (os.getenv("QQ_BOT_USAGE_REPORT", "1") or "1").strip().lower() not in (
        "0", "false", "off", "no", ""
    )


def group_id() -> str:
    return (
        os.getenv("QQ_BOT_USAGE_REPORT_GROUP", "")
        or os.getenv("QQ_BOT_ADMIN_GROUP", "")
        or ""
    ).strip()


def report_hour() -> int:
    try:
        hour = int(os.getenv("QQ_BOT_USAGE_REPORT_HOUR", "9") or 9)
    except ValueError:
        hour = 9
    return max(0, min(23, hour))


def _state_path() -> str:
    return (os.getenv("QQ_BOT_USAGE_REPORT_STATE", "") or "").strip() or STATE_PATH


def _load_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data: dict) -> None:
    path = _state_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("[日报] 写状态失败：%s", exc)


def _day_bounds(day: str) -> tuple[float, float]:
    start_dt = datetime.strptime(day, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=1)
    return start_dt.timestamp(), end_dt.timestamp()


def _fmt_pct(cost: float) -> str:
    return f"${cost:.4f}（约 ¥{cost * 7.2:.2f}）"


def _scope_name(scope: str) -> str:
    text = (scope or "").strip()
    if text.startswith("group:"):
        return "群 " + text.split(":", 1)[1][:10]
    if text.startswith("c2c:"):
        return "单聊 " + text.split(":", 1)[1][:10]
    return text or "其他"


async def build_report(usage, day: str | None = None, api=None) -> str:
    """生成某一天的日报文本；day 默认昨天。"""
    if not day:
        day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    start_ts, end_ts = _day_bounds(day)
    data = await usage.summary_between(start_ts, end_ts)
    total = data.get("total") or {}
    calls = int(total.get("calls") or 0)
    prompt = int(total.get("prompt") or 0)
    completion = int(total.get("completion") or 0)
    reasoning = int(total.get("reasoning") or 0)
    cached = int(total.get("cached") or 0)
    cost = float(total.get("cost") or 0.0)

    lines = [f"【用量日报】{day}"]
    if not calls:
        lines.append("昨天没有模型调用。")
        return "\n".join(lines)
    lines.append(
        f"调用 {calls} 次 · 输入 {prompt:,} · 输出 {completion:,} · "
        f"推理 {reasoning:,} · 缓存 {cached:,}"
    )
    lines.append(f"花费 {_fmt_pct(cost)}")
    kinds = [
        f"{_KIND_NAMES.get(k.get('kind'), k.get('kind'))} {int(k.get('calls') or 0)}"
        for k in (data.get("kinds") or [])
    ]
    if kinds:
        lines.append("类型：" + " / ".join(kinds))
    threshold = top_cny()
    scopes: list[str] = []
    for s in data.get("scopes") or []:
        scope = str(s.get("scope") or "")
        if not scope.startswith("group:"):
            continue
        cost = float(s.get("cost") or 0)
        if cost * _CNY_PER_USD < threshold:
            continue
        gid = scope.split(":", 1)[1]
        name = await _group_name(api, gid) if api else ""
        label = name or f"群 {gid[:8]}"
        scopes.append(f"{label} ¥{cost * _CNY_PER_USD:.2f}")
        if len(scopes) >= 5:
            break
    if scopes:
        lines.append(f"高用量群（>¥{threshold:g}）：" + " / ".join(scopes))
    return "\n".join(lines)


def _already_attempted_today(state: dict, today: str) -> bool:
    return (state.get("last_attempt") or "") == today


async def maybe_send(api, usage, now: datetime | None = None) -> bool:
    """到点就发一次；返回是否真的发了一条。"""
    gid = group_id()
    if not gid:
        return False
    now = now or datetime.now()
    if now.hour < report_hour():
        return False
    today = now.strftime("%Y-%m-%d")
    state = _load_state()
    if _already_attempted_today(state, today):
        return False

    report_day = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    text = await build_report(usage, report_day, api=api)
    state["last_attempt"] = today
    try:
        await api.post_group_message(group_openid=gid, content=text)
        state["last_sent"] = today
        state.pop("pending", None)
        _save_state(state)
        log.info("[日报] 已发送到管理员群 %s", gid[:10])
        return True
    except Exception as exc:  # noqa: BLE001
        # 主动消息被拒（未开「机器人主动发言」/配额）时先存成 pending：
        # 等管理员群下一次有人说话，再用被动回复补发，避免当天完全收不到。
        state["pending"] = {"day": report_day, "text": text}
        _save_state(state)
        msg = str(exc)
        if "22009" in msg or "msg limit" in msg.lower():
            log.warning(
                "[日报] 管理员群 %s 主动消息配额超限（22009），已存为待补发；"
                "让群主开启「机器人主动发言」可解除",
                gid[:10],
            )
        elif "40034105" in msg or "无权限" in msg:
            log.warning(
                "[日报] 管理员群 %s 没有主动发言权限（40034105），已存为待补发；"
                "请群主在群设置里开启「机器人主动发言」",
                gid[:10],
            )
        else:
            log.warning("[日报] 发送失败（已存为待补发）：%s", exc)
        return False


async def flush_pending(message, msg_seq: int | None = None) -> bool:
    """
    管理员群下次有人说话时，用被动回复补发 pending 日报。

    这样即使没开主动发言，也不会整天收不到；发送成功后清掉 pending。
    """
    gid = group_id()
    if not gid or getattr(message, "group_openid", "") != gid:
        return False
    state = _load_state()
    pending = state.get("pending")
    if not isinstance(pending, dict) or not pending.get("text"):
        return False
    try:
        kwargs = {"content": pending["text"]}
        if msg_seq is not None:
            kwargs["msg_seq"] = msg_seq
        await message.reply(**kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("[日报] 补发失败，保留待补发：%s", exc)
        return False
    state.pop("pending", None)
    state["last_sent"] = datetime.now().strftime("%Y-%m-%d")
    _save_state(state)
    log.info("[日报] 已用被动回复补发到管理员群 %s", gid[:10])
    return True


async def poll_loop(api, usage) -> None:
    """常驻检查：每 5 分钟看一次，过点且今天没发过就发。"""
    if not enabled():
        log.info("[日报] 未启用（QQ_BOT_USAGE_REPORT=0）")
        return
    gid = group_id()
    if not gid:
        log.info("[日报] 未配置管理员群（QQ_BOT_ADMIN_GROUP），日报不发送")
        return
    log.info(
        "[日报] 已启动：每天 %02d:00 后发到群 %s",
        report_hour(), gid[:10],
    )
    while True:
        try:
            await maybe_send(api, usage)
        except Exception as exc:  # noqa: BLE001
            log.warning("[日报] 本轮检查异常：%s", exc)
        await asyncio.sleep(CHECK_SECONDS)
