"""把 R6 战绩画成一张卡片图（PIL 手绘，纯色底、不用背景图）。

内容：头像（**带内存缓存**）+ 名字 + 段位（中文）+ 本赛季 + 近几季（右侧并列「历史最高」）
+ 近一月 RP 折线图 + 常用干员（三列网格）。

素材（头像 / 段位图标）优先联网取并缓存，**取不到就降级**（头像画灰圆、没有图标就不画），
绝不因素材缺失让整张图失败。对外只有 `render(q) -> bytes | None`。
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
PAD = 44
BG = (250, 250, 252)
FG = (33, 33, 40)
SUB = (122, 126, 136)
FOOT = (170, 174, 182)
ACCENT = (243, 148, 24)   # R6 橙
LINE = (228, 229, 234)
AVATAR = 152
CHART_H = 168

_CACHE: dict[str, bytes] = {}   # 素材内存缓存：URL -> 原始字节（头像/段位图标，进程内长期有效）


_FONTS: dict[int, object] = {}   # 字号 -> 字体对象（_load_cjk_font 每次都会重读字体文件，必须缓存）


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
        for _ in range(3):   # 育碧头像偶尔超时，重试几次
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


def _circle(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _hr(d, y: int) -> int:
    d.line((PAD, y, W - PAD, y), fill=LINE, width=2)
    return y + 26


def _chart(canvas, d, x0: int, y0: int, w: int, h: int, series: list) -> int:
    """画近一月 RP 折线图；返回下一个 y。"""
    pts = []
    for ts, rp in series or []:
        try:
            pts.append((_dt.datetime.fromisoformat(ts), float(rp)))
        except Exception:  # noqa: BLE001
            continue
    if len(pts) < 2:
        d.text((x0, y0), "RP 数据不足", font=_font(24), fill=SUB)
        return y0 + 40

    t0, t1 = pts[0][0], pts[-1][0]
    span = max((t1 - t0).total_seconds(), 1.0)
    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    if hi <= lo:
        hi = lo + 1
    pad_v = (hi - lo) * 0.18
    lo_v, hi_v = lo - pad_v, hi + pad_v

    d.rectangle((x0, y0, x0 + w, y0 + h), outline=(236, 237, 242), width=2)

    def px(dt):
        return x0 + (dt - t0).total_seconds() / span * w

    def py(v):
        return y0 + h - (v - lo_v) / (hi_v - lo_v) * h

    poly = [(px(dt), py(v)) for dt, v in pts]
    d.line(poly, fill=ACCENT, width=4, joint="curve")
    for X, Y in (poly[0], poly[-1]):
        d.ellipse((X - 6, Y - 6, X + 6, Y + 6), fill=ACCENT)

    d.text((x0 + 8, y0 + 4), f"{int(hi)}", font=_font(22), fill=SUB)
    d.text((x0 + 8, y0 + h - 28), f"{int(lo)}", font=_font(22), fill=SUB)
    d.text((x0 + 8, y0 + h + 8), t0.strftime("%m/%d"), font=_font(22), fill=FOOT)
    end = t1.strftime("%m/%d")
    d.text((x0 + w - 8 - len(end) * 12, y0 + h + 8), end, font=_font(22), fill=FOOT)
    return y0 + h + 44


def render(q: dict) -> bytes | None:
    if not q:
        return None
    try:
        return _render(q)
    except Exception:  # noqa: BLE001
        log.warning("[R6卡] 绘制失败", exc_info=True)
        return None


def _render(q: dict) -> bytes:
    canvas = Image.new("RGB", (W, 2100), BG)
    d = ImageDraw.Draw(canvas)
    y = PAD

    # ---------- 头部：头像 + 名字 + 平台/等级/时长 ----------
    avatar = _fetch(q.get("avatar")) or Image.new("RGBA", (AVATAR, AVATAR), (208, 212, 220))
    avatar = _circle(avatar, AVATAR)
    canvas.paste(avatar, (PAD, y), avatar)
    tx = PAD + AVATAR + 30
    d.text((tx, y - 8), q["name"], font=_font(46), fill=FG)
    meta = []
    if q.get("level"):
        meta.append(f"等级 {q['level']}")
    if q.get("total_hours"):
        meta.append(f"总时长 {int(q['total_hours'])}h")
    d.text((tx, y + 54), q["platform_label"] + ("　·　" + "　·　".join(meta) if meta else ""),
           font=_font(26), fill=SUB)
    peak = q.get("peak")
    if peak:
        pk = f"历史最高　{peak.get('code') or '—'}　{peak.get('rank') or '—'}"
        if peak.get("rp") is not None:
            pk += f"　{int(peak['rp'])} RP"
        d.text((tx, y + 92), pk, font=_font(24), fill=FOOT)
    y += AVATAR + 30
    y = _hr(d, y)

    # ---------- 本赛季（右上角放赛季标识，填满那一栏） ----------
    icon = _fetch(q.get("rank_icon"))
    ix = PAD
    if icon is not None:
        try:
            ic = icon.resize((62, 62), Image.LANCZOS)
            canvas.paste(ic, (ix, y - 10), ic)
            ix += 76
        except Exception:  # noqa: BLE001
            pass
    seg = q.get("rank") or "未定级"
    if q.get("rp") is not None:
        seg += f"　{int(q['rp'])} RP"
    d.text((ix, y - 2), seg, font=_font(34), fill=ACCENT)

    s = q.get("season")
    if s and s.get("matches"):
        head = f"本赛季 {s['code']} {s['name']}".rstrip()
        hw = d.textlength(head, font=_font(28))
        d.text((W - PAD - hw, y + 6), head, font=_font(28), fill=FG)
        y += 54
        wl = (f"W/L {int(s['wins'])}/{int(s['losses'])}"
              if s.get("wins") is not None else "")
        pct = f"（{s['win_rate']:.0f}%）" if s.get("win_rate") is not None else ""
        parts = [f"{int(s['matches'])} 场", f"{wl}{pct}"]
        if s.get("kd") is not None:
            parts.append(f"KD {s['kd']:.2f}")
        if s.get("hs") is not None:
            parts.append(f"HS {s['hs']:.0f}%")
        d.text((PAD + 14, y), "　".join(parts), font=_font(26), fill=SUB)
        y += 44
    else:
        y += 56
    y = _hr(d, y)

    # ---------- 近几季（每行：赛季/段位/RP/KD/KDA/胜率/爆头率） ----------
    hist = [x for x in (q.get("seasons") or []) if x.get("matches")][:3]
    if hist:
        d.text((PAD, y), "近几季", font=_font(30), fill=FG)
        y += 48
        for x in hist:
            rp = f"{int(x['rp'])} RP" if x.get("rp") is not None else "—"
            kd = f"{x['kd']:.2f}" if x.get("kd") is not None else "—"
            wl = (f"{int(x['wins'])}/{int(x['losses'])}"
                  if x.get("wins") is not None else "—")
            pct = f"（{x['win_rate']:.0f}%）" if x.get("win_rate") is not None else ""
            hs = f"{x['hs']:.0f}%" if x.get("hs") is not None else "—"
            d.text((PAD + 14, y), str(x.get("code") or "—"), font=_font(24), fill=SUB)
            d.text((PAD + 120, y), str(x.get("rank") or "—"), font=_font(24), fill=FG)
            d.text((PAD + 268, y), rp, font=_font(24), fill=SUB)
            d.text((PAD + 400, y), f"KD {kd}", font=_font(24), fill=SUB)
            d.text((PAD + 500, y), f"W/L {wl}{pct}", font=_font(24), fill=SUB)
            d.text((PAD + 780, y), f"HS {hs}", font=_font(24), fill=SUB)
            y += 40
        y += 6
    y = _hr(d, y)

    # ---------- 近一月 RP 走势 ----------
    if len(q.get("rp_history") or []) >= 2:
        d.text((PAD, y), "近一月 RP 走势", font=_font(30), fill=FG)
        y += 50
        y = _chart(canvas, d, PAD, y, W - PAD * 2, CHART_H, q["rp_history"])
        y = _hr(d, y)

    # ---------- 常用干员（三列网格） ----------
    ops = [o for o in (q.get("operators") or []) if o.get("kd") is not None]
    if ops:
        d.text((PAD, y), "常用干员", font=_font(30), fill=FG)
        y += 50
        col_w = (W - PAD * 2) // 3
        for i, o in enumerate(ops):
            col, row = i % 3, i // 3
            cx = PAD + col * col_w
            cy = y + row * 92
            d.text((cx, cy), str(o["name"]), font=_font(28), fill=FG)
            sub = f"KD {o['kd']:.2f}"
            if o.get("win") is not None:
                sub += f"　胜 {o['win']:.0f}%"
            d.text((cx, cy + 38), sub, font=_font(24), fill=SUB)
        y += ((len(ops) + 2) // 3) * 92 + 6

    # ---------- 页脚 ----------
    y += 4
    d.text((PAD, y), "数据：R6 Arenyze", font=_font(22), fill=FOOT)
    y += 42

    canvas = canvas.crop((0, 0, W, min(y, canvas.height)))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
