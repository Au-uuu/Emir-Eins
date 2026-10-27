# -*- coding: utf-8 -*-
"""
视频抽帧：把 QQ 视频转成图片序列，交给视觉模型（DS V4.1 Flash 不吃 video_url）。

规则（用户定）
--------------
- 每 15 秒抽 1 帧；
- 短视频至少 9 帧（10 秒视频也抽 9 帧，均匀分布）；
- 长视频按 15 秒一帧，最多 MAX_FRAMES（默认 30，防止一次喂太多图）。

配置
----
| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_BOT_VIDEO_MODE` | `frames` | `frames`=抽帧；`url`=直接把 video_url 交给模型（GLM）；`off`=不处理 |
| `QQ_BOT_VIDEO_FRAME_INTERVAL` | `15` | 每多少秒一帧 |
| `QQ_BOT_VIDEO_MIN_FRAMES` | `9` | 至少抽几帧 |
| `QQ_BOT_VIDEO_MAX_FRAMES` | `30` | 最多抽几帧 |
| `QQ_BOT_VIDEO_MAX_MB` | `40` | 视频下载上限 |
| `QQ_BOT_FFMPEG` | `ffmpeg` | ffmpeg 路径 |
| `QQ_BOT_VIDEO_TIMEOUT` | `180` | ffmpeg 超时（秒） |

只依赖 ffmpeg/ffprobe，不引入 Python 第三方库；失败一律返回空列表，不影响聊天。
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import tempfile
import urllib.request

log = logging.getLogger("qqbot.video")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _env(name: str, default: str) -> str:
    return (os.getenv(name, "") or default).strip()


def mode() -> str:
    return _env("QQ_BOT_VIDEO_MODE", "frames").lower()


def enabled() -> bool:
    return mode() not in ("off", "none", "0", "false")


def _num(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(_env(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


def frame_plan(duration: float) -> tuple[int, float]:
    """返回 (帧数, 采样间隔秒)。短视频补到 MIN_FRAMES，长视频按 INTERVAL。"""
    interval = _num("QQ_BOT_VIDEO_FRAME_INTERVAL", 15.0, 1.0, 120.0)
    min_frames = int(_num("QQ_BOT_VIDEO_MIN_FRAMES", 9, 1, 20))
    max_frames = int(_num("QQ_BOT_VIDEO_MAX_FRAMES", 30, min_frames, 60))
    if duration <= 0:
        return min_frames, interval
    n = max(min_frames, int(math.ceil(duration / interval)))
    n = min(max_frames, n)
    step = duration / n if n else interval
    return n, max(0.2, step)


def _duration(path: str) -> float:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", path,
            ],
            capture_output=True,
            timeout=20,
        ).stdout.decode("utf-8", "replace").strip()
        return float(out)
    except Exception:  # noqa: BLE001
        return 0.0


def extract(path: str) -> list[tuple[str, bytes]]:
    """从本地视频文件抽帧，返回 [(mime, bytes)]；失败返回 []。"""
    if not os.path.exists(path):
        return []
    ffmpeg = shutil.which(_env("QQ_BOT_FFMPEG", "ffmpeg")) or _env("QQ_BOT_FFMPEG", "ffmpeg")
    timeout = _num("QQ_BOT_VIDEO_TIMEOUT", 180.0, 20.0, 600.0)
    n, step = frame_plan(_duration(path))
    tmpdir = tempfile.mkdtemp(prefix="qqbot_frames_")
    try:
        pattern = os.path.join(tmpdir, "f_%03d.jpg")
        cmd = [
            ffmpeg, "-v", "error", "-i", path,
            "-vf", f"fps=1/{step:.3f},scale=768:-2",
            "-frames:v", str(n), "-q:v", "3", pattern,
        ]
        subprocess.run(cmd, capture_output=True, timeout=timeout)
        files = sorted(
            os.path.join(tmpdir, f)
            for f in os.listdir(tmpdir)
            if f.endswith(".jpg")
        )
        frames = []
        for f in files[:n]:
            with open(f, "rb") as fh:
                data = fh.read()
            if data:
                frames.append(("image/jpeg", data))
        return frames
    except Exception as exc:  # noqa: BLE001
        log.warning("[视频] 抽帧失败：%s", exc)
        return []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def frames_from_url(url: str) -> list[tuple[str, bytes]]:
    """下载视频 → 抽帧；超大/超时/格式异常都返回 []。"""
    if not url:
        return []
    max_mb = _num("QQ_BOT_VIDEO_MAX_MB", 40.0, 1.0, 200.0)
    tmp = ""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (QQBot)"}
        )
        with _OPENER.open(req, timeout=30) as resp:
            length = resp.headers.get("Content-Length")
            if length and int(length) > max_mb * 1024 * 1024:
                log.warning("[视频] 超过 %.0fMB，跳过：%s", max_mb, length)
                return []
            data = resp.read(int(max_mb * 1024 * 1024) + 1)
        if len(data) > max_mb * 1024 * 1024:
            log.warning("[视频] 超过 %.0fMB，跳过", max_mb)
            return []
        fd, tmp = tempfile.mkstemp(prefix="qqbot_video_", suffix=".mp4")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        frames = extract(tmp)
        log.info("[视频] 抽帧 %d 张（%.1fMB）", len(frames), len(data) / 1024 / 1024)
        return frames
    except Exception as exc:  # noqa: BLE001
        log.warning("[视频] 下载失败：%s", exc)
        return []
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
