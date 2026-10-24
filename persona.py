"""
人格聊天：把消息交给通义千问角色模型（qwen-flash-character）生成回复。

为什么是 DashScope
------------------
国内直连、延迟低；`qwen-flash-character` 便宜且自带内容安全；官方提供
OpenAI 兼容接口，用标准库 `urllib` 就能调，**不引入新依赖**。

角色卡
------
`persona/依蜜尔爱因.md`，整份作为 system prompt 传给模型。纯文本和带图
两条链路用的是**同一张卡**，人设不因换模型而变。

看图
----
`qwen-flash-character` 是纯文本模型，用户发图时改调视觉模型
（`QQ_BOT_QWEN_VL_MODEL`，默认 `qwen3-vl-flash`）：图片转 base64 data URI
放进 OpenAI 多模态 content，角色卡与历史照传。设为 `off` 可整体关闭看图。

配置（.env）
------------
| 变量 | 说明 |
|---|---|
| `QQ_BOT_QWEN_KEY` | **文本链路**的 DashScope API Key。留空则人格聊天整体关闭 |
| `QQ_BOT_QWEN_MODEL` | 文本模型，默认 `qwen-flash-character` |
| `QQ_BOT_QWEN_URL` | 文本链路的 OpenAI 兼容端点（默认 DashScope） |
| `QQ_BOT_TEXT_KEY` | **文本链路**独立 API Key（优先于 `QQ_BOT_QWEN_KEY`），如 DeepSeek |
| `QQ_BOT_TEXT_URL` | **文本链路**独立端点（优先于 `QQ_BOT_QWEN_URL`） |
| `QQ_BOT_TEXT_MODEL` | **文本链路**模型名（优先于 `QQ_BOT_QWEN_MODEL`） |
| `QQ_BOT_QWEN_VL_MODEL` | 视觉模型名（兼容旧配置），默认 `qwen3-vl-flash`，`off` 关闭看图 |
| `QQ_BOT_VL_MODEL` | 视觉模型名（优先于 `QQ_BOT_QWEN_VL_MODEL`） |
| `QQ_BOT_VL_KEY` | **视觉链路**独立的 API Key；留空则回退到 `QQ_BOT_QWEN_KEY` |
| `QQ_BOT_VL_URL` | **视觉链路**独立的端点；留空则回退到 `QQ_BOT_QWEN_URL` |
| `QQ_BOT_VL_IMAGE_FORMAT` | 图片来源格式：`data_uri`（默认，OpenAI/DashScope）或 `base64`（智谱裸 base64） |
| `QQ_BOT_VL_THINKING` | 思考模式 `enabled`/`disabled`（智谱 GLM-4.6V 系列）；留空不带该字段 |
| `QQ_BOT_VL_DESCRIBE` | 两阶段看图：`1` = GLM 先客观描述、再由 qwen 转人设；留空/`0` = 单次调用（现状） |
| `QQ_BOT_PERSONA_FILE` | 角色卡路径，默认 `persona/依蜜尔爱因.md` |
| `QQ_BOT_CHAT_TIMEOUT` | 单次请求超时秒数，默认 20 |
| `QQ_BOT_CHAT_MAX_TOKENS` | 回复长度上限，默认 512 |

> **文本/视觉可分开用不同厂商**：文本走 DashScope（`qwen-flash-character` 人设模型），
> 视觉可切到智谱 GLM-4.6V-Flash（免费）。只配 `QQ_BOT_VL_URL` 不配 `QQ_BOT_VL_KEY`
> （或反过来）会被视为配置错误、自动禁用看图，避免拿 A 家的 Key 去调 B 家的端点。
>
> **两阶段模式**（`QQ_BOT_VL_DESCRIBE=1`）：视觉模型只做**客观描述**（用下方
> `DESCRIBE_PROMPT`，不带人设），描述再拼进输入交给文本链路（`qwen-flash-character`）
> 生成符合人设的回复。好处是视觉用免费 GLM、人设始终由角色模型产出；代价是多一次调用。

⚠️ **配置必须惰性读取**：`bot.py` 里 `load_dotenv()` 在 import 语句之后才执行，
如果本模块在 import 期间就把环境变量读进模块常量，读到的会是空值（踩过这个坑）。

职责边界：本模块只负责「历史 + 这句话 → 一句回复」，**不做存储、不做过滤、不发送**。
记忆在 `chat_store`，过滤在 `sensitive`，发送在 `bot.py`。
"""

from __future__ import annotations

import asyncio
import base64
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


def vl_model() -> str:
    # 视觉模型：优先 QQ_BOT_VL_MODEL，兼容旧的 QQ_BOT_QWEN_VL_MODEL
    return _env("QQ_BOT_VL_MODEL") or _env("QQ_BOT_QWEN_VL_MODEL") or "qwen3-vl-flash"


def vl_key() -> str:
    # 视觉链路的 Key：留空则回退到文本链路的 Key（同一厂商时无需单独配）
    return _env("QQ_BOT_VL_KEY") or api_key()


def vl_url() -> str:
    # 视觉链路的端点：留空则回退到文本链路的端点
    return _env("QQ_BOT_VL_URL") or api_url()


def vl_image_format() -> str:
    # 图片来源格式：data_uri（OpenAI/DashScope，默认）或 base64（智谱裸 base64，无前缀）
    return (_env("QQ_BOT_VL_IMAGE_FORMAT") or "data_uri").lower()


def vl_thinking() -> str:
    # 思考模式（智谱 GLM-4.6V 系列）：enabled/disabled；留空 = 不带该字段、用厂商默认
    return _env("QQ_BOT_VL_THINKING").lower()


_TRUTHY = {"1", "true", "on", "yes"}


def describe_mode() -> bool:
    """两阶段看图开关：视觉模型只做客观描述，人设回复交给文本链路。"""
    return (_env("QQ_BOT_VL_DESCRIBE") or "").lower() in _TRUTHY


# 两阶段 / KB 兜底里给视觉模型用的「客观描述」提示词：只描述、不代入人设。
# 这条描述会拿去**检索知识库**，所以要求按外貌要素逐项写准——避免「蓝色头发的少女」
# 这种放到谁身上都行的宽泛描述（那样会同时命中一堆蓝发角色，等于没检索）。
DESCRIBE_PROMPT = (
    "你是图像理解助手。请客观、准确地描述用户发来的画面。这段描述会被用作「以文搜图」"
    "式检索的查询，所以外貌要素必须**写准、写具体**，不要用泛泛的形容词。按顺序逐项写：\n"
    "① 主体与画风（动漫/游戏/真人/截图/表情包…）；\n"
    "② 每个角色的性别、发色与发型（长度、扎法、刘海分法等）、瞳色、表情；\n"
    "③ 服装与配色（具体款式：水手服/西装/制服/和服…）；\n"
    "④ 标志性配件、头饰、道具（蝴蝶结、帽子、发饰、玩偶、武器…）；\n"
    "⑤ 动作姿态、场景背景、画面里的文字。\n"
    "只写你**确实看到**的；颜色写具体（「浅蓝」不要只写「蓝」）；不确定的角色名/作品名"
    "**一律不要报**，绝不编造。用简体中文，直接给描述，不做评价、不代入任何角色。"
)


# 关掉看图的写法：QQ_BOT_VL_MODEL=off / QQ_BOT_QWEN_VL_MODEL=off（大小写均可）
_VL_OFF = {"off", "none", "0", "false"}


def _vision_provider_ok() -> bool:
    """视觉用独立厂商时 URL 与 KEY 必须成对配置；只配一个视为配置错误（禁用看图）。"""
    return bool(_env("QQ_BOT_VL_URL")) == bool(_env("QQ_BOT_VL_KEY"))


def vision_enabled() -> bool:
    """带图消息走视觉模型的前提：人格聊天可用、视觉模型没被关掉、且视觉厂商配置完整。"""
    return (
        available()
        and vl_model().lower() not in _VL_OFF
        and _vision_provider_ok()
    )


def api_url() -> str:
    return _env("QQ_BOT_QWEN_URL") or (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )


# 文本链路可切厂商：QQ_BOT_TEXT_* 优先，留空回退到 DashScope 的 QWEN_* 配置。
# 例（DeepSeek，OpenAI 兼容）：
#   QQ_BOT_TEXT_KEY=sk-...
#   QQ_BOT_TEXT_URL=https://api.deepseek.com/chat/completions
#   QQ_BOT_TEXT_MODEL=deepseek-v4-pro
def text_key() -> str:
    return _env("QQ_BOT_TEXT_KEY") or api_key()


def text_url() -> str:
    return _env("QQ_BOT_TEXT_URL") or api_url()


def text_model() -> str:
    return _env("QQ_BOT_TEXT_MODEL") or model()


def prompt_file() -> str:
    return _env("QQ_BOT_PERSONA_FILE") or os.path.join(
        BASE_DIR, "persona", "依蜜尔爱因.md"
    )


def timeout() -> float:
    try:
        return float(_env("QQ_BOT_CHAT_TIMEOUT") or 20)
    except ValueError:
        return 20.0


def vl_timeout(video: bool = False) -> float:
    """
    视觉/描述链路的超时（秒）。图片通常几秒，但**视频理解很慢**（示例 11MB 视频 >30s），
    所以视频单独放大到 180s；图片默认 60s。文本链路仍用 timeout()。
    """
    name = "QQ_BOT_VL_VIDEO_TIMEOUT" if video else "QQ_BOT_VL_TIMEOUT"
    default = 180 if video else 60
    try:
        return float(_env(name) or default)
    except ValueError:
        return float(default)


def max_tokens() -> int:
    try:
        return int(_env("QQ_BOT_CHAT_MAX_TOKENS") or 512)
    except ValueError:
        return 512


# ---------------- 角色卡 ----------------

# 自身功能速查：bot.py 启动时用 HELP_TEXT 注入（单一事实源，跟 /help 永远一致）。
# 起因：用户问「怎么删除图库来着」，角色卡里没有机器人自己的用法，模型只能
# 凭空编「长按图标」（2026-10-23 实锤）。拼进系统提示词而不是消息里——静态、
# 不随消息变长，每轮多几百 token 换「答得上来自己的功能」。
_self_manual = ""


def set_self_manual(text: str) -> None:
    global _self_manual
    _self_manual = (text or "").strip()


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


def system_prompt() -> str | None:
    """角色卡 + 自身功能速查，作为系统提示词。"""
    prompt = load_prompt()
    if prompt and _self_manual:
        return (
            prompt
            + "\n\n【自身功能速查】用户问「怎么删图库/怎么添加/有什么指令」等关于"
            + "你（这台机器人）自身用法的问题时，按下面的事实回答（用你自己的口吻），"
            + "不知道就说不知道，禁止编造界面上不存在的操作：\n"
            + _self_manual
        )
    return prompt


def available() -> bool:
    """API Key 和角色卡都在，才启用人格聊天。"""
    return bool(text_key()) and load_prompt() is not None


# ---------------- 生成 ----------------


async def generate(history: list[dict], text: str) -> str | None:
    """生成一句回复。失败（网络/超时/内容安全拦截）一律返回 None，由调用方静默。"""
    text = (text or "").strip()
    if not text:
        return None
    prompt = system_prompt()
    key = text_key()
    if not prompt or not key:
        return None
    return await _request_with_retry(prompt, key, history, text)


async def generate_vision(
    history: list[dict], text: str, images: list[tuple[str, bytes]]
) -> str | None:
    """
    带图消息走视觉模型。images 是 (mime, bytes) 列表，至少一张。

    人设与纯文本链路完全一致：同一张角色卡、同一段历史、同样的静默策略，
    唯一的区别是模型名和 content 里多了图片。text 允许为空（只发图）。
    """
    if not images:
        return None
    prompt = system_prompt()
    key = vl_key()
    if not prompt or not key:
        return None
    return await _request_with_retry(
        prompt, key, history, (text or "").strip(), list(images)
    )


async def describe(
    images: list[tuple[str, bytes]],
    video_url: str | None = None,
    hint_text: str = "",
) -> str | None:
    """
    两阶段模式的第一步：让视觉模型只做**客观描述**（用 DESCRIBE_PROMPT，不带人设）。

    返回描述文本；失败返回 None（调用方回退到单次视觉调用或纯文本）。
    describe 用视觉链路（vl_key/vl_url/vl_model），与角色扮演无关。
    """
    if not images and not video_url:
        return None
    key = vl_key()
    if not key:
        return None
    return await _request_with_retry(
        DESCRIBE_PROMPT,
        key,
        [],
        (hint_text or "").strip(),
        list(images),
        video_url,
    )


def _user_content(
    text: str,
    images: list[tuple[str, bytes]],
    video_url: str | None = None,
) -> str | list:
    """
    纯文本消息 content 用字符串；带图/视频消息用 OpenAI 多模态数组：
    媒体在前（图片 data URI / 裸 base64，或视频 URL），文字在后。
    """
    images = images or []
    if not images and not video_url:
        return text
    as_base64 = vl_image_format() == "base64"
    parts: list[dict] = []
    for mime, data in images:
        b64 = base64.b64encode(data).decode("ascii")
        # 智谱要裸 base64；OpenAI/DashScope 要 data URI
        url = b64 if as_base64 else f"data:{mime};base64,{b64}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    if video_url:
        # 智谱 GLM-4.6V 系列支持视频：直接给 URL
        parts.append({"type": "video_url", "video_url": {"url": video_url}})
    if text:
        parts.append({"type": "text", "text": text})
    return parts


class _Retryable(Exception):
    """可重试的瞬时错误（限流 429 / 5xx）。"""


def _request_sync(
    prompt: str,
    key: str,
    history: list[dict],
    text: str,
    images: list[tuple[str, bytes]] | None = None,
    video_url: str | None = None,
) -> str | None:
    has_media = bool(images) or bool(video_url)
    messages = [{"role": "system", "content": prompt}]
    messages.extend(history)
    messages.append(
        {"role": "user", "content": _user_content(text, images or [], video_url)}
    )

    payload = {
        "model": vl_model() if has_media else text_model(),
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": max_tokens(),
    }
    # 智谱 GLM-4.6V 系列的思考模式开关（留空则不带该字段，用厂商默认）
    if has_media and vl_thinking() in ("enabled", "disabled"):
        payload["thinking"] = {"type": vl_thinking()}
    url = vl_url() if has_media else text_url()
    req_timeout = vl_timeout(bool(video_url)) if has_media else timeout()
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=req_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # 内容安全拦截通常也是 4xx，把响应体带出来便于排查（截断，避免刷屏）
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        log.warning("模型返回 %s：%s", exc.code, body)
        # 限流/服务端抖动：抛给上层重试；其它 4xx（内容安全等）直接静默
        if exc.code in (429, 500, 502, 503, 504):
            raise _Retryable(f"HTTP {exc.code}") from exc
        return None

    choices = data.get("choices") or []
    if not choices:
        log.warning("模型返回里没有 choices：%s", str(data)[:200])
        return None
    reply = ((choices[0].get("message") or {}).get("content") or "").strip()
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS].rstrip() + "…"
    return reply or None


async def _request_with_retry(*args, attempts: int = 3, delay: float = 1.2) -> str | None:
    """
    在线程池里跑 `_request_sync`，对可重试错误（429/5xx）做有限重试。

    免费视觉模型（GLM-4.6V-Flash）会突发 429「访问量过大」，重试通常即可恢复；
    其它失败（内容安全、网络错误）直接返回 None，由调用方静默。
    """
    for i in range(attempts):
        try:
            return await asyncio.to_thread(_request_sync, *args)
        except _Retryable as exc:
            if i < attempts - 1:
                await asyncio.sleep(delay * (i + 1))
                continue
            log.warning("重试 %d 次后仍失败：%s", attempts, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("模型调用失败，本轮静默：%s", exc)
            return None
    return None
