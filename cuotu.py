# -*- coding: utf-8 -*-
"""搓图：把一句话画进角色表情的空白气泡里。

当前只内置「睦子米说」模板（4 张底图，随机选一张）。
模板素材来自 MIT 项目 MemeCrafters/meme-generator 的扩展仓库
anyliew/meme_emoji（`mygo_wakaba_mutsumi_say`），文字区域沿用原模板坐标。
字体使用 Noto Sans SC Bold（SIL Open Font License）。

对外接口：
    split_args("睦子米 今天也不想上班") -> ("睦子米", "今天也不想上班")
    render("睦子米", "今天也不想上班") -> JPEG bytes
    list_text() / help_text()
"""

from __future__ import annotations

import io
import logging
import os
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("qqbot.cuotu")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(BASE_DIR, "assets")
MEME_DIR = os.path.join(ASSET_DIR, "memes")
FONT_DIR = os.path.join(ASSET_DIR, "fonts")

# 气泡区域有限；超过这个长度即使缩到最小字号也会糊成一团
MAX_TEXT = 24
PAD = 8
MIN_FONTSIZE = 10
MAX_FONTSIZE = 96

# 找不到内置字体时的兜底（服务器/本机常见路径）
_FALLBACK_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)


class CuotuError(Exception):
    """搓图基础异常。"""


class UnknownTemplate(CuotuError):
    """表情名不存在。"""


class TextTooLong(CuotuError):
    """文字太长，气泡塞不下。"""


class AssetsMissing(CuotuError):
    """模板图或字体缺失。"""


# 每个模板：目录名 + 图片/文字区域。坐标来自原模板（图片原始尺寸不同）。
_TEMPLATES: dict[str, dict] = {
    "mutsumi": {
        "label": "睦子米",
        "aliases": (
            "睦子米", "若叶睦", "睦头人", "小黄瓜", "睦",
            "mutsumi", "mygo_wakaba_mutsumi_say",
        ),
        "dir": "mygo_wakaba_mutsumi_say",
        "images": (
            ("1.png", (31, 89, 166, 175)),
            ("2.png", (40, 48, 192, 139)),
            ("3.png", (24, 33, 180, 140)),
            ("4.png", (27, 47, 172, 141)),
        ),
    },
}

# 别名 -> 模板 key（大小写不敏感）
_ALIASES: dict[str, str] = {}
for _key, _tpl in _TEMPLATES.items():
    for _alias in _tpl["aliases"]:
        _ALIASES[_alias.strip().casefold()] = _key


def normalize_name(name: str) -> str | None:
    """表情名/别名 -> 模板 key；不认识返回 None。"""
    return _ALIASES.get((name or "").strip().casefold())


def split_args(arg: str) -> tuple[str, str]:
    """把指令参数拆成「表情名」和「文字」。

    只按第一个空白拆分，后面的空格原样保留，例如
    `睦子米 今天 不想 上班` -> ("睦子米", "今天 不想 上班")。
    """
    text = (arg or "").strip()
    if not text:
        return "", ""
    parts = text.split(None, 1)
    name = parts[0]
    body = parts[1].strip() if len(parts) > 1 else ""
    return name, body


def _font_path() -> str | None:
    bundled = os.path.join(FONT_DIR, "NotoSansSC-Bold.ttf")
    for path in (bundled, *_FALLBACK_FONTS):
        if path and os.path.isfile(path):
            return path
    return None


def _template_image(path: str) -> Image.Image:
    with Image.open(path) as raw:
        rgba = raw.convert("RGBA")
    # 透明背景压到白底，避免转 RGB 后背景发黑
    bg = Image.new("RGB", rgba.size, (255, 255, 255))
    bg.paste(rgba, mask=rgba.getchannel("A"))
    return bg


@lru_cache(maxsize=8)
def _load_template_image(path: str) -> Image.Image:
    return _template_image(path)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
          max_width: float) -> list[str]:
    """按字符换行；中文逐字切，英文也允许在字符间断开。"""
    lines: list[str] = []
    paragraphs = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for paragraph in paragraphs:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for ch in paragraph:
            candidate = current + ch
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = ch
        lines.append(current)
    return lines


def _fit(draw: ImageDraw.ImageDraw, text: str, area: tuple[int, int, int, int],
         font_path: str):
    """从大到小找能塞进气泡的字号；返回 (font, lines, bboxes, heights, gap)。"""
    x1, y1, x2, y2 = area
    max_width = max(1, x2 - x1 - PAD * 2)
    max_height = max(1, y2 - y1 - PAD * 2)
    top = min(MAX_FONTSIZE, max_height)
    for size in range(top, MIN_FONTSIZE - 1, -1):
        font = ImageFont.truetype(font_path, size)
        lines = _wrap(draw, text, font, max_width)
        if any(draw.textlength(line, font=font) > max_width for line in lines):
            continue
        boxes = [draw.textbbox((0, 0), line or " ", font=font) for line in lines]
        heights = [box[3] - box[1] for box in boxes]
        gap = max(1, round(size * 0.12))
        total = sum(heights) + gap * (len(lines) - 1)
        if total <= max_height:
            return font, lines, boxes, heights, gap
    raise TextTooLong(text)


def _draw_text(im: Image.Image, area: tuple[int, int, int, int], text: str) -> None:
    font_path = _font_path()
    if not font_path:
        raise AssetsMissing("没有可用字体")
    draw = ImageDraw.Draw(im)
    font, lines, boxes, heights, gap = _fit(draw, text, area, font_path)
    x1, y1, x2, y2 = area
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    total = sum(heights) + gap * (len(lines) - 1)
    y = cy - total / 2.0
    for line, box, height in zip(lines, boxes, heights):
        width = box[2] - box[0]
        draw.text(
            (cx - width / 2.0 - box[0], y - box[1]),
            line,
            font=font,
            fill=(0, 0, 0),
        )
        y += height + gap


def render(name: str, text: str, number: int = 0) -> bytes:
    """生成图片字节。number=0 随机，1 起为指定底图（测试用）。"""
    key = normalize_name(name)
    if not key:
        raise UnknownTemplate(name)
    body = (text or "").strip()
    if not body:
        raise TextTooLong("空文字")
    if len(body) > MAX_TEXT:
        raise TextTooLong(body)

    template = _TEMPLATES[key]
    images = template["images"]
    if number:
        if not 1 <= number <= len(images):
            raise UnknownTemplate(f"{name}#{number}")
        index = number - 1
    else:
        index = random.randrange(len(images))

    filename, area = images[index]
    path = os.path.join(MEME_DIR, template["dir"], filename)
    if not os.path.isfile(path):
        raise AssetsMissing(path)

    im = _load_template_image(path).copy()
    _draw_text(im, area, body)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue()


def template_count() -> int:
    return len(_TEMPLATES)


def list_text() -> str:
    lines = ["当前支持的表情："]
    for tpl in _TEMPLATES.values():
        aliases = [a for a in tpl["aliases"][:4] if a != tpl["label"]]
        suffix = f"（别名：{' / '.join(aliases)}）" if aliases else ""
        lines.append(f"　{tpl['label']}{suffix}　{len(tpl['images'])} 张底图")
    lines.append("")
    lines.append("用法：/搓图 睦子米 今天也不想上班")
    return "\n".join(lines)


def help_text() -> str:
    return (
        "【搓图 · 用法】\n"
        "/搓图 表情名 要加的字\n"
        "　第一个空格前是表情名，后面全部算文字；\n"
        "　同一表情有多张底图时随机选一张。\n"
        "\n"
        "例：/搓图 睦子米 今天也不想上班\n"
        "　　/搓图 睦子米 我从没觉得玩乐队开心过\n"
        "\n"
        "看表情：/搓图 列表\n"
        f"限制：文字最多 {MAX_TEXT} 字，气泡塞不下会自动缩小/换行。"
    )
