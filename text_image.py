"""文本转图片：把长文本渲染成在 QQ 里显示更紧凑的图片。

为什么需要
----------
QQ 的文本消息字号是固定的，帮助这类长文本一发出来就占满整屏，观感差。

转成图片则不同：**QQ 会把图片等比缩放到聊天窗口的宽度**。所以只要把画布画得
足够宽、字号相对画布足够小，缩放后在手机上字就变小、一屏能看下更多内容 ——
这正是「显示得小一点」的实现原理：

    屏上字号 ≈ font_size × (聊天窗口宽度 / 画布宽度)

经验值（手机聊天窗口约 400pt 宽）：

| 画布宽度 | 缩放比 | 28px 字号在屏上约 |
|---|---|---|
| 1080 | 0.37 | 10.4pt |
| 1280 | 0.31 | 8.7pt |
| 1600 | 0.25 | 7.0pt |

另：图片越长，QQ 只按宽度缩放，不会因为高就变小，所以排版不受影响。

用法
----
    from text_image import render_text_image

    png = render_text_image(HELP_TEXT, width=1280, font_size=28)
    # png 是 PNG bytes，可直接交给 uploader 发送

    # 想更快地试各种密度：
    for w in (1080, 1280, 1600):
        open(f"help_{w}.png", "wb").write(render_text_image(HELP_TEXT, width=w))

注意
----
QQ 的「按钮」（keyboard）只能挂在**文本消息**上，换成图片消息后按钮就带不了了。
所以 /help 转图需要决定：要么带按钮用文本，要么纯图片。
"""

from __future__ import annotations

import io
from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw

# 复用项目里已有的「找一个可用的中文字体」逻辑（image_store 内部私有函数，
# 同包内复用，避免两份字体查找表各自漂移）
from image_store import _load_cjk_font

# 配色：浅底深字，与名片拼图保持同一套观感
BG = (250, 250, 252)
FG = (33, 33, 40)

DEFAULTS = {
    "width": 1280,
    "font_size": 28,
    "padding": 40,
    "line_spacing": 1.45,   # 行距倍数
    "paragraph_gap": 0.55,  # 空行额外增加的高度（按行高倍数）
    "supersample": 2,       # 先按 2 倍尺寸绘制再缩小，边缘更干净
}


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> List[str]:
    """按像素宽度折行。

    中文逐字断行；英文/数字尽量在空格处断开，断不开才硬切。
    """
    lines: List[str] = []
    for raw in text.split("\n"):
        if not raw.strip():
            lines.append("")
            continue
        cur = ""
        for ch in raw:
            if draw.textlength(cur + ch, font=font) <= max_w:
                cur += ch
                continue
            # 放不下：英文词优先在最后一个空格处断
            if ch.isascii() and not ch.isspace() and " " in cur.strip():
                head, _, tail = cur.rpartition(" ")
                if head:
                    lines.append(head)
                    cur = tail + ch
                    continue
            lines.append(cur)
            cur = ch
        lines.append(cur)
    return lines


def render_text_image(
    text: str,
    *,
    width: int = DEFAULTS["width"],
    font_size: int = DEFAULTS["font_size"],
    padding: int = DEFAULTS["padding"],
    line_spacing: float = DEFAULTS["line_spacing"],
    paragraph_gap: float = DEFAULTS["paragraph_gap"],
    supersample: int = DEFAULTS["supersample"],
    bg: Tuple[int, int, int] = BG,
    fg: Tuple[int, int, int] = FG,
    max_height: int = 20000,
) -> bytes:
    """把 ``text`` 渲染成 PNG bytes。

    :param width: 画布宽度（像素）。越大 → 在 QQ 里显示越小。
    :param font_size: 字号（像素）。与 ``width`` 的比值决定观感。
    :param max_height: 高度上限，超出则抛 ``ValueError``，避免生成畸形长图。
    """
    if width <= 0 or font_size <= 0:
        raise ValueError("width / font_size 必须为正数")

    ss = max(1, int(supersample))
    w, pad, fs = width * ss, padding * ss, font_size * ss
    font = _load_cjk_font(fs)

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    max_text_w = w - pad * 2
    lines = _wrap(probe, text, font, max_text_w)

    ascent, descent = font.getmetrics()
    line_h = (ascent + descent) * line_spacing
    total_h = pad * 2
    for ln in lines:
        total_h += line_h * (1 + paragraph_gap) if not ln else line_h
    total_h = int(round(total_h))

    if total_h > max_height * ss:
        raise ValueError(
            f"渲染高度 {total_h // ss}px 超过上限 {max_height}px，请加大宽度或减小字号"
        )

    img = Image.new("RGB", (w, total_h), bg)
    draw = ImageDraw.Draw(img)

    y = float(pad)
    for ln in lines:
        if ln:
            draw.text((pad, y), ln, font=font, fill=fg)
            y += line_h
        else:
            y += line_h * paragraph_gap

    if ss > 1:
        img = img.resize((width, max(1, int(round(total_h / ss)))), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_lines_image(
    lines: Sequence[str],
    **kwargs,
) -> bytes:
    """按行渲染（等价于 ``render_text_image("\\n".join(lines))``）。"""
    return render_text_image("\n".join(lines), **kwargs)
