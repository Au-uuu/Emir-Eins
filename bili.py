# -*- coding: utf-8 -*-
"""B 站视频解析：链接 → 标题/UP/简介 + 抽帧。

为什么不直接抓网页/交给 yt-dlp：
- B 站会给人机验证，机房 IP 打开视频页常见 412；本机实测 `yt-dlp` 也 412。
- 但官方 JSON 接口在带上浏览器请求头 + `buvid3` Cookie 后可以正常返回。
- 拿到 DASH 的视频流（不含音频）后用 ffmpeg 抽帧，再交给视觉模型。

配置
----
| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_BOT_BILI` | `1` | `0` 关闭 B 站解析 |
| `QQ_BOT_BILI_MAX_MB` | `40` | 视频流下载上限（超出只取前 40MB） |
| `QQ_BOT_BILI_INTERVAL` | `10` | 每隔多少秒抽一帧 |
| `QQ_BOT_BILI_MIN_FRAMES` | `9` | 至少抽几帧 |
| `QQ_BOT_BILI_MAX_FRAMES` | `24` | 最多抽几帧 |
| `QQ_BOT_BILI_MAX_DESC` | `300` | 简介最多带多少字 |

失败一律返回空结果，不影响聊天。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import urllib.request

import video_frames

log = logging.getLogger("qqbot.bili")

API = "https://api.bilibili.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?bilibili\.com/video/(?:BV[0-9A-Za-z]{10}|av\d+)"
    r"|https?://b23\.tv/[0-9A-Za-z]+",
    re.I,
)
_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"/video/av(\d+)", re.I)

_cookie_lock = threading.Lock()
_cookie: tuple[float, str] = (0.0, "")
_view_cache: dict[str, tuple[float, dict]] = {}
_view_lock = threading.Lock()


def enabled() -> bool:
    return (os.getenv("QQ_BOT_BILI", "1") or "1").strip().lower() not in (
        "0", "false", "off", "no", "",
    )


def _num(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float((os.getenv(name, "") or str(default)).strip())
    except ValueError:
        value = default
    return max(low, min(high, value))


def pick_url(text: str) -> str:
    """从一段文字里取第一个 B 站视频链接；没有返回空串。"""
    match = _URL_RE.search(text or "")
    return match.group(0) if match else ""


def _base_headers() -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://www.bilibili.com",
        "Referer": "https://www.bilibili.com/",
    }


def _buvid_cookie(force: bool = False) -> str:
    """取一个匿名 buvid3 Cookie；B 站接口没有它容易被 412。"""
    global _cookie
    now = time.time()
    with _cookie_lock:
        if not force and _cookie[1] and now - _cookie[0] < 3600:
            return _cookie[1]
        try:
            req = urllib.request.Request(
                f"{API}/x/frontend/finger/spi", headers=_base_headers()
            )
            with _OPENER.open(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            b3 = (data.get("data") or {}).get("b_3") or ""
            if b3:
                _cookie = (now, f"buvid3={b3}")
                return _cookie[1]
        except Exception as exc:  # noqa: BLE001
            log.warning("[B站] 获取 buvid3 失败：%s", exc)
        return _cookie[1]


def _get_json(url: str, retry: bool = True) -> dict:
    headers = _base_headers()
    cookie = _buvid_cookie()
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with _OPENER.open(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    code = data.get("code")
    if code in (-412, -352) and retry:
        # 风控码：换个 buvid3 再试一次
        log.info("[B站] 触发风控 %s，刷新 Cookie 重试", code)
        _buvid_cookie(force=True)
        return _get_json(url, retry=False)
    return data


def _resolve(url: str) -> str:
    """展开 b23.tv 短链，返回最终 URL。"""
    if "b23.tv" not in url:
        return url
    try:
        req = urllib.request.Request(url, headers=_base_headers())
        with _OPENER.open(req, timeout=15) as resp:
            final = resp.geturl()
            if final and "b23.tv" not in final:
                return final
            # 有些跳转只给 Location，不跟随时手动取一次
            location = resp.headers.get("Location")
            if location:
                return location
    except Exception as exc:  # noqa: BLE001
        log.warning("[B站] 短链展开失败：%s", exc)
    return url


def _view(bvid: str = "", aid: str = "") -> dict:
    key = bvid or f"av{aid}"
    now = time.time()
    with _view_lock:
        hit = _view_cache.get(key)
        if hit and now - hit[0] < 600:
            return hit[1]
    query = f"bvid={bvid}" if bvid else f"aid={aid}"
    data = _get_json(f"{API}/x/web-interface/view?{query}")
    if data.get("code") != 0:
        raise RuntimeError(f"view 接口返回 {data.get('code')}: {data.get('message')}")
    view = data.get("data") or {}
    if not view:
        raise RuntimeError("view 接口没有数据")
    with _view_lock:
        _view_cache[key] = (now, view)
        if len(_view_cache) > 50:
            for old in sorted(_view_cache, key=lambda k: _view_cache[k][0])[:10]:
                _view_cache.pop(old, None)
    return view


def _play_url(view: dict, qn: int = 32) -> str:
    bvid = view.get("bvid") or ""
    cid = view.get("cid")
    if not cid:
        return ""
    url = f"{API}/x/player/playurl?bvid={bvid}&cid={cid}&qn={qn}&fnval=16&fourk=0"
    data = _get_json(url)
    if data.get("code") != 0:
        raise RuntimeError(f"playurl 接口返回 {data.get('code')}: {data.get('message')}")
    payload = data.get("data") or {}
    dash = payload.get("dash") or {}
    streams = dash.get("video") or []
    if streams:
        # 优先 avc（兼容性最好），再按清晰度/带宽从低到高，抽帧不需要高清
        def rank(row: dict) -> tuple:
            codec = row.get("codecs") or ""
            return (0 if "avc" in codec else 1, row.get("height") or 0)

        usable = [row for row in streams if row.get("baseUrl")]
        if usable:
            usable.sort(key=rank)
            return usable[0]["baseUrl"]
    # 老视频没有 DASH，退回 durl
    durl = payload.get("durl") or []
    for row in durl:
        if row.get("url"):
            return row["url"]
    return ""


def _download(url: str, cap_mb: float) -> tuple[bytes, bool]:
    """下载媒体流，最多 cap_mb；返回 (数据, 是否被截断)。"""
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    req = urllib.request.Request(url, headers=headers)
    cap = int(cap_mb * 1024 * 1024)
    with _OPENER.open(req, timeout=40) as resp:
        data = resp.read(cap + 1)
    if len(data) > cap:
        return data[:cap], True
    return data, False


def _clock(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _format_meta(view: dict, frame_count: int, truncated: bool) -> str:
    title = (view.get("title") or "").strip()
    owner = ((view.get("owner") or {}).get("name") or "").strip()
    duration = _clock(view.get("duration") or 0)
    desc = (view.get("desc") or "").strip()
    desc = " ".join(desc.split())
    max_desc = int(_num("QQ_BOT_BILI_MAX_DESC", 300, 0, 1000))
    if max_desc and len(desc) > max_desc:
        desc = desc[:max_desc] + "…"
    parts = [f"【B 站视频】{title or '（无标题）'}"]
    meta = []
    if owner:
        meta.append(f"UP 主：{owner}")
    meta.append(f"时长：{duration}")
    parts.append("｜".join(meta))
    if desc:
        parts.append(f"简介：{desc}")
    if frame_count:
        tail = f"（按时间顺序抽出 {frame_count} 帧"
        tail += "；视频较长，只取了前段" if truncated else ""
        parts.append(tail + "）")
    return "\n".join(parts)


def parse(url: str, want_frames: bool = True) -> tuple[str, list[tuple[str, bytes]]]:
    """解析一个 B 站视频链接，返回 (元数据文本, 帧列表)。失败返回 ("", [])。"""
    if not enabled() or not url:
        return "", []
    tmp = ""
    try:
        url = _resolve(url)
        bv = _BV_RE.search(url)
        av = _AV_RE.search(url)
        if bv:
            view = _view(bvid=bv.group(1))
        elif av:
            view = _view(aid=av.group(1))
        else:
            log.info("[B站] 链接里没有 BV/av 号：%s", url)
            return "", []

        if not want_frames:
            return _format_meta(view, 0, False), []

        play = _play_url(view)
        if not play:
            log.warning("[B站] 没取到可播放流：%s", view.get("bvid"))
            return _format_meta(view, 0, False), []

        cap_mb = _num("QQ_BOT_BILI_MAX_MB", 40.0, 5.0, 200.0)
        data, truncated = _download(play, cap_mb)
        if not data:
            return _format_meta(view, 0, False), []

        fd, tmp = tempfile.mkstemp(prefix="qqbot_bili_", suffix=".m4s")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        interval = _num("QQ_BOT_BILI_INTERVAL", 10.0, 2.0, 120.0)
        min_frames = int(_num("QQ_BOT_BILI_MIN_FRAMES", 9, 1, 20))
        max_frames = int(_num("QQ_BOT_BILI_MAX_FRAMES", 24, min_frames, 60))
        frames = video_frames.extract(tmp, interval, min_frames, max_frames)
        log.info(
            "[B站] %s 抽帧 %d 张（%.1fMB%s）",
            view.get("bvid"), len(frames), len(data) / 1024 / 1024,
            "，已截断" if truncated else "",
        )
        return _format_meta(view, len(frames), truncated), frames
    except Exception as exc:  # noqa: BLE001
        log.warning("[B站] 解析失败 %s：%s", url, exc)
        return "", []
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
