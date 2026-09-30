"""
人格聊天：把消息交给通义千问角色模型（qwen-flash-character）生成回复。

为什么是 DashScope
------------------
国内直连、延迟低；`qwen-flash-character` 便宜且自带内容安全；官方提供
OpenAI 兼容接口，用标准库 `urllib` 就能调，**不引入新依赖**。

角色卡
------
`persona/依蜜尔爱因.md`，整份作为 system prompt 传给模型。

配置（.env）
------------
| 变量 | 说明 |
|---|---|
| `QQ_BOT_QWEN_KEY` | DashScope API Key。**留空则人格聊天整体关闭** |
| `QQ_BOT_QWEN_MODEL` | 默认 `qwen-flash-character` |
| `QQ_BOT_QWEN_URL` | 默认 DashScope 的 OpenAI 兼容端点 |
| `QQ_BOT_PERSONA_FILE` | 角色卡路径，默认 `persona/依蜜尔爱因.md` |
| `QQ_BOT_CHAT_TIMEOUT` | 单次请求超时秒数，默认 20 |
| `QQ_BOT_CHAT_MAX_TOKENS` | 回复长度上限，默认 512 |

⚠️ **配置必须惰性读取**：`bot.py` 里 `load_dotenv()` 在 import 语句之后才执行，
如果本模块在 import 期间就把环境变量读进模块常量，读到的会是空值（踩过这个坑）。

职责边界：本模块只负责「历史 + 这句话 → 一句回复」，**不做存储、不做过滤、不发送**。
记忆在 `chat_store`，过滤在 `sensitive`，发送在 `bot.py`。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("qqbot.persona")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 单条回复的字数上限：QQ 文本消息放太长会很难看，也容易触发平台长度限制
MAX_REPLY_CHARS = 400

_prompt_cache: str | None = None
_prompt_loaded = False


# ---------------- 配置（惰性读取，见模块 docstring） ----------------


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def api_key() -> str:
    return _env("QQ_BOT_QWEN_KEY")


def model() -> str:
    return _env("QQ_BOT_QWEN_MODEL") or "qwen-flash-character"


def api_url() -> str:
    return _env("QQ_BOT_QWEN_URL") or (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )


def prompt_file() -> str:
    return _env("QQ_BOT_PERSONA_FILE") or os.path.join(
        BASE_DIR, "persona", "依蜜尔爱因.md"
    )


def timeout() -> float:
    try:
        return float(_env("QQ_BOT_CHAT_TIMEOUT") or 20)
    except ValueError:
        return 20.0


def max_tokens() -> int:
    try:
        return int(_env("QQ_BOT_CHAT_MAX_TOKENS") or 512)
    except ValueError:
        return 512


# ---------------- 角色卡 ----------------


def load_prompt() -> str | None:
    """读取角色卡（只读一次）。读不到则返回 None。"""
    global _prompt_cache, _prompt_loaded
    if _prompt_loaded:
        return _prompt_cache
    _prompt_loaded = True
    path = prompt_file()
    try:
        with open(path, encoding="utf-8") as fh:
            _prompt_cache = fh.read().strip() or None
    except OSError as exc:
        log.warning("读取角色卡失败，人格聊天关闭：%s（%s）", path, exc)
        _prompt_cache = None
    if _prompt_cache:
        log.info("角色卡已加载：%s（%d 字）", path, len(_prompt_cache))
    return _prompt_cache


def available() -> bool:
    """API Key 和角色卡都在，才启用人格聊天。"""
    return bool(api_key()) and load_prompt() is not None


# ---------------- 生成 ----------------


async def generate(history: list[dict], text: str) -> str | None:
    """生成一句回复。失败（网络/超时/内容安全拦截）一律返回 None，由调用方静默。"""
    text = (text or "").strip()
    if not text:
        return None
    prompt = load_prompt()
    key = api_key()
    if not prompt or not key:
        return None
    try:
        return await asyncio.to_thread(_request_sync, prompt, key, history, text)
    except Exception as exc:  # noqa: BLE001
        log.warning("模型调用失败，本轮静默：%s", exc)
        return None


def _request_sync(
    prompt: str, key: str, history: list[dict], text: str
) -> str | None:
    messages = [{"role": "system", "content": prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": text})

    payload = {
        "model": model(),
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": max_tokens(),
    }
    req = urllib.request.Request(
        api_url(),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout()) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # 内容安全拦截通常也是 4xx，把响应体带出来便于排查（截断，避免刷屏）
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        log.warning("模型返回 %s：%s", exc.code, body)
        return None

    choices = data.get("choices") or []
    if not choices:
        log.warning("模型返回里没有 choices：%s", str(data)[:200])
        return None
    reply = ((choices[0].get("message") or {}).get("content") or "").strip()
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS].rstrip() + "…"
    return reply or None
