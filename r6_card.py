"""把 R6 战绩画成一张卡片图（PIL 手绘，纯色底、不用背景图）。

素材：头像（`account.profilePicture`）、当前段位图标（RP 历史最新一条的 `imageUrl`）。
两者都优先联网取，**取不到就降级**（头像画成灰圆、没有图标就不画），绝不因素材缺失
让整张图失败。字体复用 image_store 的 CJK 字体加载。

对外只有一个 `render(q) -> bytes | None`；失败返回 None，调用方退回文本。
"""

from __future__ import annotations

import io
import logging
import urllib.request

from PIL import Image, ImageDraw

from image_store import _load_cjk_font

log = logging.getLogger("qqbot.r6card")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) qqbot-r6/1.0"}

W = 1000
PAD = 44
BG = (250, 250, 252)
FG = (33, 33, 40)
SUB = (122, 126, 136)
FOOT = (168, 172, 180)
ACCENT = (243, 148, 24)   # R6 橙
LINE = (228, 229, 234)
AVATAR = 152


def _font(size: int):
    return _load_cjk_font(size)


def _fetch(url: str | None):
    if not url:
        return None
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=15
        ).read()
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        log.info("[R6卡] 素材下载失败：%s", exc)
        return None


def _circle(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def render(q: dict) -> bytes | None:
    """画卡片；失败返回 None。"""
    if not q:
        return None
    try:
        return _render(q)
    except Exception:  # noqa: BLE001
        log.warning("[R6卡] 绘制失败", exc_info=True)
        return None


def _render(q: dict) -> bytes:
    canvas = Image.new("RGB", (W, 1800), BG)
    d = ImageDraw.Draw(canvas)
    y = PAD

    # ---------- 头部：头像 + 名字 + 平台/等级/时长 + 段位 ----------
    avatar = _fetch(q.get("avatar")) or Image.new("RGBA", (AVATAR, AVATAR), (208, 212, 220))
    avatar = _circle(avatar, AVATAR)
    canvas.paste(avatar, (PAD, y), avatar)

    tx = PAD + AVATAR + 30
    d.text((tx, y - 6), q["name"], font=_font(46), fill=FG)
    meta = []
    if q.get("level"):
        meta.append(f"等级 {q['level']}")
    if q.get("total_hours"):
        meta.append(f"总时长 {int(q['total_hours'])}h")
    line2 = q["platform_label"] + ("　·　" + "　·　".join(meta) if meta else "")
    d.text((tx, y + 62), line2, font=_font(26), fill=SUB)

    ry = y + 112
    icon = _fetch(q.get("rank_icon"))
    ix = tx
    if icon is not None:
        try:
            icon = icon.resize((58, 58), Image.LANCZOS)
            canvas.paste(icon, (ix, ry - 8), icon)
            ix += 70
        except Exception:  # noqa: BLE001
            pass
    seg = q.get("rank") or "未定级"
    if q.get("rp") is not None:
        seg += f"　{int(q['rp'])} RP"
    if q.get("max_rp") is not None:
        seg += f"　（最高 {int(q['max_rp'])}）"
    d.text((ix, ry), seg, font=_font(32), fill=ACCENT)

    y += AVATAR + 30
    d.line((PAD, y, W - PAD, y), fill=LINE, width=2)
    y += 26

    # ---------- 本赛季 ----------
    s = q.get("season")
    if s and s.get("matches"):
        d.text((PAD, y), f"本赛季 {s['code']} {s['name']}".rstrip(), font=_font(30), fill=FG)
        y += 48
        wr = f"{int(s['wins'])} 胜 {int(s['losses'])} 负" if s.get("wins") is not None else ""
        pct = f"（{s['win_rate']:.0f}%）" if s.get("win_rate") is not None else ""
        kd = f"　KD {s['kd']:.2f}" if s.get("kd") is not None else ""
        d.text((PAD + 14, y), f"{int(s['matches'])} 场　{wr}{pct}{kd}", font=_font(27), fill=SUB)
        y += 54

    # ---------- 近几季 ----------
    hist = [x for x in (q.get("seasons") or []) if x.get("matches")][:3]
    if hist:
        d.line((PAD, y, W - PAD, y), fill=LINE, width=2)
        y += 22
        d.text((PAD, y), "近几季", font=_font(30), fill=FG)
        y += 48
        for x in hist:
            rp = f"{int(x['rp'])} RP" if x.get("rp") is not None else "—"
            kd = f"KD {x['kd']:.2f}" if x.get("kd") is not None else "KD —"
            d.text((PAD + 14, y), str(x.get("code") or "—"), font=_font(27), fill=SUB)
            d.text((PAD + 200, y), str(x.get("rank") or "—"), font=_font(27), fill=FG)
            d.text((PAD + 540, y), rp, font=_font(27), fill=SUB)
            d.text((PAD + 730, y), kd, font=_font(27), fill=SUB)
            y += 44
        y += 6

    # ---------- 常用干员 ----------
    ops = [o for o in (q.get("operators") or []) if o.get("kd") is not None]
    if ops:
        d.line((PAD, y, W - PAD, y), fill=LINE, width=2)
        y += 22
        d.text((PAD, y), "常用干员", font=_font(30), fill=FG)
        y += 48
        for o in ops:
            w = f"胜 {o['win']:.0f}%" if o.get("win") is not None else ""
            d.text((PAD + 14, y), str(o["name"]), font=_font(27), fill=FG)
            d.text((PAD + 320, y), f"KD {o['kd']:.2f}", font=_font(27), fill=SUB)
            d.text((PAD + 540, y), w, font=_font(27), fill=SUB)
            y += 44

    # ---------- 页脚 ----------
    y += 8
    d.text((PAD, y), "数据：R6 Arenyze", font=_font(22), fill=FOOT)
    y += 40

    canvas = canvas.crop((0, 0, W, min(y, canvas.height)))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
