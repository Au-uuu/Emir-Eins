"""
原始事件 payload 拦截。

背景：botpy 在 `ConnectionState.parse_xxx` 里只取 `payload["d"]` 交给消息对象，
原始的 payload（含 `msg_elements`、`message_scene`）会被丢弃。

而「引用一条图片消息」时，QQ 平台**不会**在 top-level 给出 `attachments`，
图片藏在 `d.msg_elements[].attachments[].url` 里——只有拿到原始 payload 才能读到。

这里猴补丁 socket 解析层，把原始 payload 按消息 ID 暂存，供处理器查询。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import OrderedDict
from typing import Any

log = logging.getLogger("qqbot.raw")

# 事件名 -> 是否已拦截
_PATCHED = False

# message_id -> (存入时间, payload)
_STORE: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_TTL = 300.0  # 与被动回复窗口对齐
_MAXSIZE = 500

# 可选：把原始事件落盘，便于排查（环境变量 QQ_BOT_DUMP_EVENTS=1 开启）
_DUMP = os.getenv("QQ_BOT_DUMP_EVENTS", "").lower() in ("1", "true", "yes")
_DUMP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs", "raw_events.jsonl"
)


def _remember(msg_id: str, payload: dict) -> None:
    now = time.monotonic()
    while _STORE:
        _, (ts, _) = next(iter(_STORE.items()))
        if now - ts > _TTL:
            _STORE.popitem(last=False)
        else:
            break
    if msg_id:
        _STORE[msg_id] = (now, payload)
        if len(_STORE) > _MAXSIZE:
            _STORE.popitem(last=False)

    if _DUMP:
        try:
            os.makedirs(os.path.dirname(_DUMP_PATH), exist_ok=True)
            with open(_DUMP_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            pass


def get_raw(message) -> dict:
    """取回某条消息对应的原始 payload["d"]，没有则返回空字典。"""
    msg_id = getattr(message, "id", None)
    if not msg_id:
        return {}
    entry = _STORE.get(msg_id)
    if entry is None:
        return {}
    payload = entry[1]
    d = payload.get("d")
    return d if isinstance(d, dict) else {}


def _install_missing_parsers() -> None:
    """
    补齐 botpy 1.2.1 缺失的「群全量消息」解析器。

    群主一旦开启「接收所有消息」，群内每条消息（**包括 @机器人**）都会以
    GROUP_MESSAGE_CREATE 事件推送。但 botpy 1.2.1 的 ConnectionState 里
    没有 parse_group_message_create，网关只能报 `Unknown event` 直接丢弃，
    表现为「单聊能回、群里 @ 完全不理人」。

    注意：ConnectionState.parsers 是在 __init__ 里用 inspect.getmembers
    一次性收集的，所以必须在实例化之前补到类上。
    """
    from botpy.connection import ConnectionState

    if hasattr(ConnectionState, "parse_group_message_create"):
        return

    from botpy.message import GroupMessage

    def parse_group_message_create(self, payload):
        _message = GroupMessage(
            self.api, payload.get("id", None), payload.get("d", {})
        )
        self._dispatch("group_message_create", _message)

    ConnectionState.parse_group_message_create = parse_group_message_create
    log.info("已补齐 GROUP_MESSAGE_CREATE 事件解析器（SDK 1.2.1 缺失）")


def install() -> None:
    """安装拦截器。重复调用是安全的。"""
    global _PATCHED
    if _PATCHED:
        return

    from botpy.connection import ConnectionState

    # 先把 SDK 缺失的事件解析器补上，下面的拦截循环才能包到它
    _install_missing_parsers()

    events = [
        "group_at_message_create",
        "group_message_create",
        "c2c_message_create",
    ]

    for event in events:
        attr = f"parse_{event}"
        original = getattr(ConnectionState, attr, None)
        if original is None:
            # 该 SDK 版本没有这个事件解析器
            log.debug("跳过不存在的解析器: %s", attr)
            continue

        def make(orig):
            def wrapper(self, payload: Any):
                try:
                    data = payload.get("d") if isinstance(payload, dict) else None
                    if isinstance(data, dict):
                        _remember(str(data.get("id") or ""), payload)
                except Exception:  # noqa: BLE001
                    log.exception("暂存原始事件失败")
                return orig(self, payload)

            return wrapper

        setattr(ConnectionState, attr, make(original))

    _PATCHED = True
    log.info("原始事件拦截已安装")
