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

    # 带背景图：铺满画布 + 自动压一层半透明圆角面板保证可读
    png = render_text_image(HELP_TEXT, width=1280, background="help_bg.png")
    # 背景越花，面板越要不透明：panel_alpha=230
    # 想要深色海报风：panel_color=(20,20,24), panel_alpha=180, fg=(245,245,245)

注意
----
`/help` 原本挂的三个内嵌按钮已经**移除**（实测点击从未生效，见
`docs/changes/2026-10-02-help-image.md`）。所以本模块只需考虑纯图片：
图片发不出去时由调用方退回文本。
"""

from __future__ import annotations

import io
from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter

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


def _cover_image(path: str, width: int, height: int, max_width: int = 0) -> Image.Image:
    """把背景图按「覆盖」方式铺满画布（等比缩放 + 居中裁剪，不变形）。

    :param max_width: 先把原图缩到这个宽度上限再铺（0=不缩）。
        照片的**细节量**才是 JPEG 体积的大头，先缩一次能压得多得多；
        代价是背景变柔（对「衬在文字后面」的用途通常无所谓，甚至更好看）。
    """
    with Image.open(path) as raw:
        img = raw.convert("RGB")
    if max_width and img.width > max_width:
        ratio = max_width / img.width
        img = img.resize(
            (max_width, max(1, round(img.height * ratio))), Image.LANCZOS
        )
    scale = max(width / img.width, height / img.height)
    new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    img = img.resize(new_size, Image.LANCZOS)
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


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
    background: str | None = None,
    panel_color: Tuple[int, int, int] = (255, 255, 255),
    panel_alpha: int = 214,
    panel_radius: int = 28,
    panel_margin: int = 26,
    image_top: int = 0,
    panel_blur: int = 0,
    columns: int = 1,
    column_gap: int = 56,
    background_max: int = 0,
) -> bytes:
    """把 ``text`` 渲染成 PNG bytes。

    :param width: 画布宽度（像素）。越大 → 在 QQ 里显示越小。
    :param font_size: 字号（像素）。与 ``width`` 的比值决定观感。
    :param max_height: 高度上限，超出则抛 ``ValueError``，避免生成畸形长图。
    :param background: 背景图路径。给了就铺满画布，并在其上盖一层半透明圆角面板，
        保证文字在任何花哨背景上都读得清（不给就是纯色底）。
    :param panel_alpha: 面板不透明度（0-255）。背景越花，越需要调高。
    :param image_top: 面板**上方**额外留出的背景高度（像素）。用于竖版构图：
        上面露插画、下面放文字面板。
    :param panel_blur: 把面板**后面的背景**模糊多少像素（0=不模糊）。
        这是做出「磨砂玻璃」的关键：模糊后背景依然可见，但不再和文字抢注意力，
        于是面板可以做到很透也依然清晰。
    :param columns: 分几栏排版。**2 栏能在宽度不变（字一样小）的前提下把高度砍掉近一半**，
        既省流量又少滚动；分栏点会挑最靠近中点的空行，避免把一组指令劈成两半。
    :param background_max: 背景图先缩到这个宽度上限再铺（0=不缩）。照片的**细节量**是
        JPEG 体积的大头，缩一次能显著减小文件；代价是背景变柔。
    """
    if width <= 0 or font_size <= 0:
        raise ValueError("width / font_size 必须为正数")

    ss = max(1, int(supersample))
    w, pad, fs = width * ss, padding * ss, font_size * ss
    font = _load_cjk_font(fs)

    cols = 2 if int(columns) >= 2 else 1
    gap = column_gap * ss if cols == 2 else 0
    col_w = (w - pad * 2 - gap * (cols - 1)) // cols

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = _wrap(probe, text, font, col_w)

    ascent, descent = font.getmetrics()
    line_h = (ascent + descent) * line_spacing

    if cols == 2:
        # 分栏点挑「最靠近中点的空行」——帮助文本用空行分隔指令组，
        # 在这里切就不会把一组指令劈成两栏。
        mid = len(lines) // 2
        split = None
        for i, ln in enumerate(lines):
            if not ln and (split is None or abs(i - mid) < abs(split - mid)):
                split = i
        if not split:
            split = mid
        col_lines = [lines[:split], lines[split:]]
        while col_lines[1] and not col_lines[1][0]:
            col_lines[1].pop(0)
    else:
        col_lines = [lines]

    def _block_height(rows: list) -> float:
        h = 0.0
        for ln in rows:
            h += line_h * (1 + paragraph_gap) if not ln else line_h
        return h

    top = max(0, int(image_top)) * ss
    total_h = int(round(top + pad * 2 + max(_block_height(r) for r in col_lines)))

    if total_h > max_height * ss:
        raise ValueError(
            f"渲染高度 {total_h // ss}px 超过上限 {max_height}px，请加大宽度或减小字号"
        )

    if background:
        img = _cover_image(background, w, total_h, background_max * ss)
        m = panel_margin * ss
        panel_box = [m, m + top, w - m, total_h - m]

        # 磨砂玻璃：先把面板覆盖到的背景模糊掉，再叠半透明面板。
        # 于是面板可以做得**很透**（背景仍然看得见），文字却不会被背景细节干扰。
        if panel_blur > 0:
            blurred = img.filter(ImageFilter.GaussianBlur(panel_blur * ss))
            mask = Image.new("L", (w, total_h), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                panel_box, radius=panel_radius * ss, fill=255
            )
            img = Image.composite(blurred, img, mask)

        overlay = Image.new("RGBA", (w, total_h), (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle(
            panel_box,
            radius=panel_radius * ss,
            fill=panel_color + (panel_alpha,),
        )
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    else:
        img = Image.new("RGB", (w, total_h), bg)
    draw = ImageDraw.Draw(img)

    for idx, rows in enumerate(col_lines):
        x = pad + idx * (col_w + gap)
        y = float(top + pad)
        for ln in rows:
            if ln:
                draw.text((x, y), ln, font=font, fill=fg)
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
