"""把 `/help` 渲染成一张带背景图的图片。

为什么
------
QQ 文本消息的字号是固定的，「帮助」这种长文本一发出来就占满整屏。转成图片后 QQ 会
按聊天窗口宽度**等比缩放**，画布越宽、缩放越多、字越小 —— 一屏就能看下更多内容
（原理与参数说明见 `text_image.py`）。

配置（.env）
------------
| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_BOT_HELP_BG` | `data/help_bg.jpg` | 背景图。**这张图不存在就自动退回纯文本** |
| `QQ_BOT_HELP_WIDTH` | 1280 | 画布宽度（越大 → 在 QQ 里显示越小） |
| `QQ_BOT_HELP_FONTSIZE` | 28 | 字号 |
| `QQ_BOT_HELP_ALPHA` | 120 | 面板不透明度（越小背景越透、**文件越大**） |
| `QQ_BOT_HELP_QUALITY` | 50 | JPEG 质量 |
| `QQ_BOT_HELP_COLUMNS` | 1 | 分栏数（实测两栏更差，别开） |
| `QQ_BOT_HELP_LINESPACING` | 1.45 | 行距倍数（越小图越矮、文件越小） |
| `QQ_BOT_HELP_PARAGAP` | 0.55 | 空行额外高度倍数 |
| `QQ_BOT_HELP_BGMAX` | 0 | 背景先缩到这个宽度再铺（0=不缩） |

为什么输出 JPEG
---------------
带照片背景时 PNG 压不动（1280 宽约 **2.2MB**，3Mbps 上传要 6 秒）。
当前参数下 JPEG 约 **153KB**（群/普通人版）与 **200KB**（管理员版），上传 1 秒级。

排版是**按用户指定的那一版保留**（字号 28 / 面板 120 / 原行距段距）——即最初
`help_SERVER.jpg` 的观感。所以体积只能靠质量与色度采样压：
`q50 + 4:2:0` 比 `q85 + 4:4:4` 小 **60%**（381→153KB）。

几个实测结论（详见 `docs/changes/2026-10-02-help-image.md`）：

- **面板越透明，文件越大**：背景细节透出来 = 熵更高。所以「更透明」与「更小」
  本质矛盾；要明显更小就得动面板不透明度或版面密度。
- **这里用 4:2:0 而非 4:4:4**：正文是深灰字配浅色面板，几乎没有色度信息，
  4:2:0 画质损失很小、体积明显更小（同质量下 444 要贵 10~15%）。
- **「背景柔化」在低质量下没用**：q55 早已把照片细节丢光，再缩背景一个字节不省。

缓存
----
帮助文本是静态的，所以只渲染一次，之后返回内存里的同一份字节。
"""

from __future__ import annotations

import io
import logging
import os
import threading

from PIL import Image

from text_image import render_text_image

log = logging.getLogger("qqbot.help")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BG = os.path.join(BASE_DIR, "data", "help_bg.jpg")

_lock = threading.Lock()
_cache: dict[str, bytes | None] = {}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def bg_path() -> str:
    return os.getenv("QQ_BOT_HELP_BG", "") or DEFAULT_BG


def render(text: str) -> bytes | None:
    """渲染帮助图，返回 JPEG 字节；没配背景图或渲染失败则返回 None（调用方退回文本）。"""
    with _lock:
        if text in _cache:
            return _cache[text]

    data = _render(text)

    with _lock:
        _cache[text] = data
    return data


def _render(text: str) -> bytes | None:
    path = bg_path()
    if not path or not os.path.isfile(path):
        log.info("未配置帮助背景图，/help 保持文本形态：%s", path)
        return None

    try:
        png = render_text_image(
            text,
            width=_int_env("QQ_BOT_HELP_WIDTH", 1280),
            font_size=_int_env("QQ_BOT_HELP_FONTSIZE", 28),
            line_spacing=_float_env("QQ_BOT_HELP_LINESPACING", 1.45),
            paragraph_gap=_float_env("QQ_BOT_HELP_PARAGAP", 0.55),
            background=path,
            panel_alpha=_int_env("QQ_BOT_HELP_ALPHA", 120),
            columns=_int_env("QQ_BOT_HELP_COLUMNS", 1),
            background_max=_int_env("QQ_BOT_HELP_BGMAX", 0),
        )
        img = Image.open(io.BytesIO(png)).convert("RGB")
        buf = io.BytesIO()
        # subsampling=2 -> 4:2:0。正文是深灰字配浅色面板，几乎没有色度信息，
        # 这个场景下 4:2:0 的画质损失很小、体积明显更小（实测过才这么选）。
        img.save(
            buf,
            format="JPEG",
            quality=_int_env("QQ_BOT_HELP_QUALITY", 50),
            optimize=True,
            subsampling=2,
        )
        data = buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        log.warning("渲染帮助图失败，退回文本：%s", exc)
        return None

    log.info(
        "帮助图已生成：%dx%d %.0f KB（背景 %s）",
        img.width,
        img.height,
        len(data) / 1024,
        path,
    )
    return data
