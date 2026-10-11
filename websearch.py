"""联网搜索（两步式）：单独让便宜模型联网取一段事实摘要，再注入角色模型。

为什么不直接在人设模型上开 `enable_search`：实测会把约 6.5K 的检索**原文**塞进
prompt，而且模型会变成「新闻播报」、人设崩。两步式只把约 200~300 token 的摘要
注入角色模型，人设稳、角色侧 token 也从 11K 降到 ~4.8K。

失败/未启用一律返回空串——联网是聊天的加分项，不能拖垮回复（与 kb/tagger 同策略）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request

log = logging.getLogger("qqbot.websearch")

DEFAULT_MODEL = "qwen-flash"
DEFAULT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# 给「检索助手」的指令：只要事实、别客套、别演角色
_SEARCH_PROMPT = (
    "你是检索助手。用联网搜索，就用户的问题给出准确、最新的事实要点，200字以内；"
    "只给事实，不做评价、不扮演任何角色、不要客套。"
)

# 只在「像在问实时信息」时才搜，避免每条都联网（一次约 6.5K 输入 token + 可能有的搜索费）。
# 只认「话题/意图」词，不认「今天/最近」这类纯时间词——否则「今天心情如何」也会误触发。
_TRIGGER_DEFAULT = (
    r"新闻|头条|动态|进展|消息|情报|新番|新作|新游|"
    r"什么时候|哪天|几号|几点|多少钱|价格|股价|汇率|天气|比分|"
    r"出了|上线|发布|更新|新版本|版本更新|联动|活动|开服|停服|维护|"
    r"结果(是|如何|出来)|发生(了|什么)"
)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def enabled() -> bool:
    return _env("QQ_BOT_WEBSEARCH").lower() in {"1", "true", "on", "yes"}


def _trigger_re() -> re.Pattern:
    return re.compile(_env("QQ_BOT_WEBSEARCH_TRIGGERS") or _TRIGGER_DEFAULT, re.I)


def should_search(text: str) -> bool:
    """启用了、且文本命中时效性触发词，才值得联网。"""
    text = (text or "").strip()
    return bool(text) and enabled() and bool(_trigger_re().search(text))


def _key() -> str:
    return _env("QQ_BOT_WEBSEARCH_KEY") or _env("QQ_BOT_QWEN_KEY")


def _url() -> str:
    return _env("QQ_BOT_WEBSEARCH_URL") or _env("QQ_BOT_QWEN_URL") or DEFAULT_URL


def _model() -> str:
    return _env("QQ_BOT_WEBSEARCH_MODEL") or DEFAULT_MODEL


def _timeout() -> float:
    try:
        return float(_env("QQ_BOT_WEBSEARCH_TIMEOUT") or 25)
    except ValueError:
        return 25.0


def search(query: str) -> str:
    """联网检索，返回事实摘要；未启用/失败返回空串。"""
    query = (query or "").strip()
    if not query or not enabled():
        return ""
    key = _key()
    if not key:
        return ""
    payload = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": _SEARCH_PROMPT},
            {"role": "user", "content": query},
        ],
        "enable_search": True,
        "temperature": 0.2,
        "max_tokens": 350,
    }
    req = urllib.request.Request(
        _url(),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        choices = data.get("choices") or []
        if not choices:
            return ""
        out = ((choices[0].get("message") or {}).get("content") or "").strip()
        return out
    except Exception as exc:  # noqa: BLE001
        log.info("[联网] 检索失败，已忽略：%s", exc)
        return ""


def wrap(summary: str) -> str:
    """把摘要包成给角色模型的「事实资料」块（对齐知识库注入的措辞）。"""
    summary = (summary or "").strip()
    if not summary:
        return ""
    return (
        "（联网检索到以下资料，供你准确作答、语气仍按人设——其中的具体名称、数字、时间"
        "要照实保留，不要抽象成“某公司/某地”；**绝不许提「联网/搜索/检索」这类字眼**，"
        "也不要照抄资料，用本机的口吻讲）：\n" + summary + "\n"
    )
