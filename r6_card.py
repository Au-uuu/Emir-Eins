"""把 R6 战绩画成一张**深色卡片图**（PIL 手绘，不用背景图）。

风格：深底 + 面板（圆角）+ R6 橙强调色 + 色块统计 + 渐变折线。素材只保留**头像**和
**段位图标**（联网取，带内存缓存与重试；取不到就降级，不让整张图失败）。
对外只有 `render(q) -> bytes | None`。
"""

from __future__ import annotations

import datetime as _dt
import io
import logging
import urllib.request

from PIL import Image, ImageDraw

from image_store import _load_cjk_font

log = logging.getLogger("qqbot.r6card")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) qqbot-r6/1.0"}

W = 1000
PAD = 30
BG = (15, 17, 23, 255)
PANEL = (25, 28, 36, 255)
PANEL_HI = (34, 38, 48, 255)
FG = (238, 240, 245, 255)
SUB = (156, 162, 176, 255)
DIM = (108, 114, 128, 255)
ACCENT = (242, 169, 59, 255)
GREEN = (92, 200, 126, 255)
RED = (232, 96, 96, 255)
CHIP_BG = (242, 169, 59, 46)

_CACHE: dict[str, bytes] = {}   # 头像/段位图标的内存缓存（URL -> 原始字节）
_FONTS: dict[int, object] = {}  # 字号 -> 字体（_load_cjk_font 每次重读文件，必须缓存）


def _font(size: int):
    f = _FONTS.get(size)
    if f is None:
        f = _load_cjk_font(size)
        _FONTS[size] = f
    return f


def _fetch(url: str | None) -> Image.Image | None:
    if not url:
        return None
    raw = _CACHE.get(url)
    if raw is None:
        last = None
        for _ in range(3):
            try:
                raw = urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=20
                ).read()
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                raw = None
        if raw is None:
            log.info("[R6卡] 素材下载失败：%s", last)
            return None
        _CACHE[url] = raw
    try:
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        log.info("[R6卡] 素材解析失败：%s", exc)
        return None


def _rounded(img: Image.Image, size: int, radius: int) -> Image.Image:
    img = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _section(d, y: int, title: str, right: str | None = None) -> int:
    d.rectangle((PAD + 22, y + 5, PAD + 27, y + 27), fill=ACCENT)
    d.text((PAD + 40, y), title, font=_font(25), fill=FG)
    if right:
        w = d.textlength(right, font=_font(21))
        d.text((W - PAD - 24 - w, y + 5), right, font=_font(21), fill=DIM)
    return y + 42


def _tiles(d, y: int, x0: int, x1: int, items: list, h: int = 92) -> int:
    n = len(items)
    gap = 12
    tw = (x1 - x0 - gap * (n - 1)) // n
    for i, (label, value, color) in enumerate(items):
        tx = x0 + i * (tw + gap)
        d.rounded_rectangle((tx, y, tx + tw, y + h), radius=12, fill=PANEL_HI)
        d.text((tx + 16, y + 14), label, font=_font(20), fill=DIM)
        d.text((tx + 16, y + 44), value, font=_font(32), fill=color)
    return y + h


def _chart(canvas, d, x0: int, y0: int, w: int, h: int, series: list) -> int:
    pts = []
    for ts, rp in series or []:
        try:
            pts.append((_dt.datetime.fromisoformat(ts), float(rp)))
        except Exception:  # noqa: BLE001
            continue
    if len(pts) < 2:
        d.text((x0, y0), "RP 数据不足", font=_font(22), fill=DIM)
        return y0 + 40

    t0, t1 = pts[0][0], pts[-1][0]
    span = max((t1 - t0).total_seconds(), 1.0)
    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    if hi <= lo:
        hi = lo + 1
    pad_v = (hi - lo) * 0.18
    lo_v, hi_v = lo - pad_v, hi + pad_v

    def px(dt):
        return (dt - t0).total_seconds() / span * w

    def py(v):
        return h - (v - lo_v) / (hi_v - lo_v) * h

    poly = [(px(dt), py(v)) for dt, v in pts]

    overlay = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).polygon(
        [(x, y) for x, y in poly] + [(w, h), (0, h)], fill=(242, 169, 59, 52)
    )
    canvas.alpha_composite(overlay, (x0, y0))

    d.line([(x0 + x, y0 + y) for x, y in poly], fill=ACCENT, width=4, joint="curve")
    ex, ey = x0 + poly[-1][0], y0 + poly[-1][1]
    d.ellipse((ex - 6, ey - 6, ex + 6, ey + 6), fill=ACCENT)

    d.text((x0 + 2, y0 - 2), f"{int(hi)}", font=_font(20), fill=DIM)
    d.text((x0 + 2, y0 + h - 24), f"{int(lo)}", font=_font(20), fill=DIM)
    d.text((x0 + 2, y0 + h + 8), t0.strftime("%m/%d"), font=_font(20), fill=DIM)
    end = t1.strftime("%m/%d")
    d.text((x0 + w - 4 - d.textlength(end, font=_font(20)), y0 + h + 8), end,
           font=_font(20), fill=DIM)
    return y0 + h + 42


def render(q: dict) -> bytes | None:
    if not q:
        return None
    try:
        return _render(q)
    except Exception:  # noqa: BLE001
        log.warning("[R6卡] 绘制失败", exc_info=True)
        return None


def _render(q: dict) -> bytes:
    canvas = Image.new("RGBA", (W, 2600), BG)
    d = ImageDraw.Draw(canvas, "RGBA")
    y = 22

    # ================= 头部面板 =================
    hp = 176
    d.rounded_rectangle((PAD, y, W - PAD, y + hp), radius=18, fill=PANEL)
    av = _fetch(q.get("avatar"))
    asz = 132
    ax, ay = PAD + 22, y + 22
    if av is not None:
        canvas.alpha_composite(_rounded(av, asz, 16), (ax, ay))
    else:
        d.rounded_rectangle((ax, ay, ax + asz, ay + asz), radius=16, fill=PANEL_HI)

    tx = ax + asz + 24
    d.text((tx, ay + 2), q["name"], font=_font(40), fill=FG)
    # 平台色块 + 等级 + 时长（与色块垂直居中）
    cw = int(d.textlength(q["platform_label"], font=_font(20))) + 22
    d.rounded_rectangle((tx, ay + 56, tx + cw, ay + 84), radius=8, fill=(242, 169, 59, 240))
    d.text((tx + 11, ay + 60), q["platform_label"], font=_font(20), fill=(24, 27, 34))
    meta = []
    if q.get("level"):
        meta.append(f"等级 {q['level']}")
    if q.get("total_hours"):
        meta.append(f"总时长 {int(q['total_hours'])}h")
    d.text((tx + cw + 14, ay + 60), "　".join(meta), font=_font(20), fill=SUB)
    peak = q.get("peak")
    if peak:
        pk = f"历史最高　{peak.get('code') or '—'}　{peak.get('rank') or '—'}"
        if peak.get("rp") is not None:
            pk += f"　{int(peak['rp'])} RP"
        d.text((tx, ay + 96), pk, font=_font(21), fill=DIM)
    y += hp + 16

    # ================= 本赛季面板 =================
    s = q.get("season") or {}
    ph = 42 + 74 + 92 + 26
    d.rounded_rectangle((PAD, y, W - PAD, y + ph), radius=18, fill=PANEL)
    y += 20
    season_right = f"{s.get('code', '')}　{s.get('name', '')}".strip() or None
    y = _section(d, y, "本赛季", season_right)

    # 段位行：[图标] 段位名（橙） + 右侧 RP
    icon = _fetch(q.get("rank_icon"))
    ix = PAD + 24
    if icon is not None:
        canvas.alpha_composite(_rounded(icon, 58, 8), (ix, y - 6))
        ix += 74
    d.text((ix, y), q.get("rank") or "未定级", font=_font(36), fill=ACCENT)
    if q.get("rp") is not None:
        rp = f"{int(q['rp'])} RP"
        d.text((W - PAD - 24 - d.textlength(rp, font=_font(30)), y + 4), rp,
               font=_font(30), fill=ACCENT)
    y += 74

    wr = s.get("win_rate")
    tiles = []
    if s.get("kd") is not None:
        tiles.append(("K/D", f"{s['kd']:.2f}", FG))
    if wr is not None:
        tiles.append(("胜率", f"{wr:.0f}%", FG))
    if s.get("matches") is not None:
        tiles.append(("场次", f"{int(s['matches'])}", FG))
    if s.get("wins") is not None:
        tiles.append(("胜场", f"{int(s['wins'])}", GREEN))
    if s.get("losses") is not None:
        tiles.append(("败场", f"{int(s['losses'])}", RED))
    if tiles:
        y = _tiles(d, y, PAD + 24, W - PAD - 24, tiles[:5])
    y += 26

    # ================= 近几季面板 =================
    hist = [x for x in (q.get("seasons") or []) if x.get("matches")][:4]
    if hist:
        ph = 20 + 42 + len(hist) * 40 + 18
        d.rounded_rectangle((PAD, y, W - PAD, y + ph), radius=18, fill=PANEL)
        y += 20
        y = _section(d, y, "近几季")
        for x in hist:
            rp = f"{int(x['rp'])} RP" if x.get("rp") is not None else "—"
            kd = f"KD {x['kd']:.2f}" if x.get("kd") is not None else "KD —"
            w = (f"W/L {int(x['wins'])}/{int(x['losses'])}"
                 if x.get("wins") is not None else "W/L —")
            pct = f"（{x['win_rate']:.0f}%）" if x.get("win_rate") is not None else ""
            d.text((PAD + 24, y), str(x.get("code") or "—"), font=_font(24), fill=SUB)
            d.text((PAD + 140, y), str(x.get("rank") or "—"), font=_font(24), fill=FG)
            d.text((PAD + 320, y), rp, font=_font(24), fill=ACCENT)
            d.text((PAD + 470, y), kd, font=_font(24), fill=SUB)
            d.text((PAD + 600, y), f"{w}{pct}", font=_font(24), fill=SUB)
            y += 40
        y += 18
    y += 16

    # ================= RP 走势面板 =================
    if len(q.get("rp_history") or []) >= 2:
        ph = 20 + 42 + 168 + 42 + 10
        d.rounded_rectangle((PAD, y, W - PAD, y + ph), radius=18, fill=PANEL)
        y += 20
        y = _section(d, y, "近一月 RP 走势")
        y = _chart(canvas, d, PAD + 44, y, W - PAD * 2 - 68, 168, q["rp_history"])
        y += 30
    y += 16

    # ================= 常用干员面板 =================
    ops = [o for o in (q.get("operators") or []) if o.get("kd") is not None][:6]
    if ops:
        rows = (len(ops) + 2) // 3
        ph = 20 + 42 + rows * 76 + 8
        d.rounded_rectangle((PAD, y, W - PAD, y + ph), radius=18, fill=PANEL)
        y += 20
        y = _section(d, y, "常用干员")
        col_w = (W - PAD * 2 - 48) // 3
        for i, o in enumerate(ops):
            col, row = i % 3, i // 3
            cx = PAD + 24 + col * col_w
            cy = y + row * 76
            d.text((cx, cy), str(o["name"]), font=_font(26), fill=FG)
            sub = f"KD {o['kd']:.2f}"
            if o.get("win") is not None:
                sub += f"　胜 {o['win']:.0f}%"
            d.text((cx, cy + 34), sub, font=_font(22), fill=SUB)
        y += rows * 76 + 8
        y += 20
    y += 10

    d.text((PAD + 4, y), "数据：R6 Arenyze", font=_font(20), fill=DIM)
    y += 36

    canvas = canvas.crop((0, 0, W, min(y, canvas.height))).convert("RGB")
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
