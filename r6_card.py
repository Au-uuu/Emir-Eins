"""把 R6 战绩画成一张**深色卡片图**（PIL 手绘，不用背景图）。

风格：深底 + 面板（圆角）+ R6 橙强调色 + 色块统计 + 渐变折线。素材只保留**头像**和
**段位图标**（联网取，带内存缓存与重试；取不到就降级，不让整张图失败）。
对外只有 `render(q) -> bytes | None`。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import logging
import os
import time
import urllib.request

from PIL import Image, ImageDraw

from image_store import _load_cjk_font

log = logging.getLogger("qqbot.r6card")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) qqbot-r6/1.0"}

W = 1000
PAD = 30
BG = (32, 32, 32, 255)
PANEL = (34, 34, 34, 255)
PANEL_HI = (44, 44, 45, 255)
BORDER = (58, 58, 60, 255)
FG = (238, 238, 240, 255)
SUB = (168, 170, 174, 255)
DIM = (124, 126, 132, 255)
ACCENT = (223, 163, 73, 255)
GREEN = (110, 200, 134, 255)
RED = (232, 104, 104, 255)

_CACHE: dict[str, bytes] = {}   # 头像/段位图标的内存缓存（URL -> 原始字节）
_FONTS: dict[int, object] = {}  # 字号 -> 字体（_load_cjk_font 每次重读文件，必须缓存）

# 素材再落一份到磁盘：内存缓存重启就没了，而头像/段位图标要联网下载（实测首次 ~2.5 秒）。
# 磁盘缓存只当「预热」，过期或读坏了就重新下，不影响正确性。
_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "r6assets")
_ASSET_TTL = 7 * 24 * 3600.0


def _asset_path(url: str) -> str:
    return os.path.join(_ASSET_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".bin")


def _asset_get(url: str) -> bytes | None:
    try:
        p = _asset_path(url)
        if time.time() - os.path.getmtime(p) > _ASSET_TTL:
            return None
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


def _asset_put(url: str, raw: bytes) -> None:
    try:
        os.makedirs(_ASSET_DIR, exist_ok=True)
        with open(_asset_path(url), "wb") as f:
            f.write(raw)
    except OSError as exc:
        log.info("[R6卡] 素材落盘失败：%s", exc)


def _font(size: int):
    f = _FONTS.get(size)
    if f is None:
        f = _load_cjk_font(size)
        _FONTS[size] = f
    return f


def _fetch(url: str | None) -> Image.Image | None:
    if not url:
        return None
    raw = _CACHE.get(url) or _asset_get(url)
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
        _asset_put(url, raw)
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
    """小节标题：橙色竖条与标题文字**垂直居中对齐**；右侧文字也与标题居中。"""
    ft, fr = _font(25), _font(21)
    _, t, _, b = d.textbbox((0, 0), title, font=ft)
    cy = y + (t + b) / 2
    bar_h = 22
    d.rectangle((PAD + 24, cy - bar_h / 2, PAD + 29, cy + bar_h / 2), fill=ACCENT)
    d.text((PAD + 42, y), title, font=ft, fill=FG)
    if right:
        _, rt, _, rb = d.textbbox((0, 0), right, font=fr)
        d.text((W - PAD - 24 - d.textlength(right, font=fr), cy - (rt + rb) / 2),
               right, font=fr, fill=DIM)
    return y + 42


def _tiles(d, y: int, x0: int, x1: int, items: list, h: int = 92) -> int:
    n = len(items)
    gap = 12
    tw = (x1 - x0 - gap * (n - 1)) // n
    for i, (label, value, color) in enumerate(items):
        tx = x0 + i * (tw + gap)
        d.rounded_rectangle((tx, y, tx + tw, y + h), radius=12, fill=PANEL_HI,
                            outline=BORDER, width=1)
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

    from PIL import ImageChops

    fill = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
    ImageDraw.Draw(fill).polygon(
        [(x, y) for x, y in poly] + [(w, h), (0, h)], fill=ACCENT
    )
    grad = Image.linear_gradient("L").resize((1, h + 2)).point(
        lambda v: 132 - int(v * 132 / 255)
    )
    ga = fill.split()[3]
    fill.putalpha(ImageChops.multiply(ga, grad.resize((w + 2, h + 2))))
    canvas.alpha_composite(fill, (x0, y0))

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
    d.rounded_rectangle((PAD, y, W - PAD, y + hp), radius=18, fill=PANEL,
                        outline=BORDER, width=1)
    av = _fetch(q.get("avatar"))
    asz = 132
    ax, ay = PAD + 24, y + 22
    if av is not None:
        canvas.alpha_composite(_rounded(av, asz, 16), (ax, ay))
    else:
        d.rounded_rectangle((ax, ay, ax + asz, ay + asz), radius=16, fill=PANEL_HI)

    tx = ax + asz + 24
    d.text((tx, ay + 2), q["name"], font=_font(40), fill=FG)
    # 平台色块（文字在色块内精确居中）+ 等级
    # （**不显示「游戏时长」**：上游口径乱、跨账号错得没规律，见 r6stats 里那段注释）
    lab, f20 = q["platform_label"], _font(20)
    l, t, r, b = d.textbbox((0, 0), lab, font=f20)
    cw, ch = (r - l) + 26, (b - t) + 14
    cx, cy = tx, ay + 56
    d.rounded_rectangle((cx, cy, cx + cw, cy + ch), radius=8, fill=ACCENT)
    d.text((cx + 13 - l, cy + 7 - t), lab, font=f20, fill=(17, 19, 23))
    meta = []
    if q.get("level"):
        meta.append(f"等级 {q['level']}")
    if q.get("total_hours"):
        meta.append(f"总时长 {int(q['total_hours'])}h")
    d.text((cx + cw + 14, cy + 7 - t), "　".join(meta), font=f20, fill=SUB)
    peak = q.get("peak")
    if peak:
        pk = f"历史最高　{peak.get('code') or '—'}　{peak.get('rank') or '—'}"
        if peak.get("rp") is not None:
            pk += f"　{int(peak['rp'])} RP"
        d.text((tx, ay + 98), pk, font=_font(21), fill=DIM)

    # 右侧：段位徽章 + 段位名 + RP（填满头部右侧空档）
    ri = _fetch(q.get("rank_icon"))
    bsz = 96
    bx, by = W - PAD - 24 - bsz, ay + 14
    if ri is not None:
        canvas.alpha_composite(_rounded(ri, bsz, 12), (bx, by))
    else:
        d.rounded_rectangle((bx, by, bx + bsz, by + bsz), radius=12,
                            fill=PANEL_HI, outline=BORDER, width=1)
    rname = q.get("rank") or "未定级"
    rp_txt = f"{int(q['rp'])} RP" if q.get("rp") is not None else ""
    f_rn, f_rp = _font(28), _font(24)
    _, tn, _, bn = d.textbbox((0, 0), rname, font=f_rn)
    _, tp, _, bp = d.textbbox((0, 0), rp_txt, font=f_rp) if rp_txt else (0, 0, 0, 0)
    gap = 8
    block_h = (bn - tn) + (gap + (bp - tp) if rp_txt else 0)
    top = (by + bsz / 2) - block_h / 2          # 与徽章垂直居中
    d.text((bx - 22 - d.textlength(rname, font=f_rn), top - tn), rname,
           font=f_rn, fill=ACCENT)
    if rp_txt:
        d.text((bx - 22 - d.textlength(rp_txt, font=f_rp), top + (bn - tn) + gap - tp),
               rp_txt, font=f_rp, fill=SUB)
    y += hp + 16

    # ================= 本赛季面板 =================
    s = q.get("season") or {}
    ph = 42 + 92 + 26
    d.rounded_rectangle((PAD, y, W - PAD, y + ph), radius=18, fill=PANEL, outline=BORDER, width=1)
    y += 20
    season_right = f"{s.get('code', '')}　{s.get('name', '')}".strip() or None
    y = _section(d, y, "本赛季", season_right)

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
        d.rounded_rectangle((PAD, y, W - PAD, y + ph), radius=18, fill=PANEL, outline=BORDER, width=1)
        y += 20
        y = _section(d, y, "近几季")
        for x in hist:
            rp = f"{int(x['rp_show'])} RP" if x.get("rp_show") is not None else "—"
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
        d.rounded_rectangle((PAD, y, W - PAD, y + ph), radius=18, fill=PANEL, outline=BORDER, width=1)
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
        d.rounded_rectangle((PAD, y, W - PAD, y + ph), radius=18, fill=PANEL, outline=BORDER, width=1)
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

    d.text((PAD + 24, y), "数据：R6 Arenyze", font=_font(20), fill=DIM)
    y += 36

    canvas = canvas.crop((0, 0, W, min(y, canvas.height))).convert("RGB")
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
