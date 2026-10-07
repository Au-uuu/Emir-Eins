"""WD14 Tagger：本地 ONNX 动漫打标，给看图链路提供「识别线索」。

为什么
------
qwen3-vl 认不出 Danbooru 收录之外的新角色（比如 2026 年 1 月才上线的终末地）。
WD14（SmilingWolf/wd-swinv2-tagger-v3，基于 Danbooru 数百万图训练的开源 ONNX
模型）直接输出 character / copyright 标签——相当于一份内置的「特征→角色名」
速查表。标签作为线索拼进视觉提示词，中文译名由 qwen 自己完成。

模型文件（不进 git、不进 data/ 常规备份）
----------------------------------------
默认目录 models/wd-swinv2-tagger-v3/（model.onnx + selected_tags.csv）：
  curl -L https://hf-mirror.com/SmilingWolf/wd-swinv2-tagger-v3/resolve/main/model.onnx
  curl -L https://hf-mirror.com/SmilingWolf/wd-swinv2-tagger-v3/resolve/main/selected_tags.csv
文件缺失或加载失败 → 打标静默禁用，看图链路照常工作。

预处理与官方示例一致：RGBA 合成到白底、白边补方、缩放到模型输入边长、
RGB→BGR、float32。模型输出已含 sigmoid，直接与阈值比较。
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import threading

log = logging.getLogger("qqbot.tagger")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL_DIR = os.path.join(BASE_DIR, "models", "wd-swinv2-tagger-v3")

CHAR_THRESHOLD = 0.35
COPYRIGHT_THRESHOLD = 0.35
MAX_TAGS = 4

_lock = threading.Lock()
_session = None  # (session, [(tag_id, name)...], [(tag_id, name)...], size)
_failed = False


def model_dir() -> str:
    return os.getenv("QQ_BOT_TAGGER_DIR", "") or DEFAULT_MODEL_DIR


def available() -> bool:
    """模型文件齐全才可用（缺文件标记禁用，避免每张图都去 stat）。"""
    global _failed
    if _failed:
        return False
    d = model_dir()
    ok = os.path.isfile(os.path.join(d, "model.onnx")) and os.path.isfile(
        os.path.join(d, "selected_tags.csv")
    )
    if not ok:
        _failed = True
        log.info("WD14 模型文件缺失（%s），角色打标禁用", d)
    return ok


def _load():
    global _session, _failed
    if _session is not None or _failed:
        return _session is not None
    with _lock:
        if _session is not None or _failed:
            return _session is not None
        try:
            import onnxruntime as ort

            d = model_dir()
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2  # 4 核小机，给主进程留核
            sess = ort.InferenceSession(
                os.path.join(d, "model.onnx"),
                sess_options=so,
                providers=["CPUExecutionProvider"],
            )
            shape = sess.get_inputs()[0].shape
            # WD14 ONNX 是 NHWC 布局：[1, 448, 448, 3]
            size = int(shape[1]) if isinstance(shape[1], int) else 448

            chars: list[tuple[int, str]] = []
            copies: list[tuple[int, str]] = []
            with open(os.path.join(d, "selected_tags.csv"), encoding="utf-8") as fh:
                for idx, row in enumerate(csv.DictReader(fh)):
                    # 输出向量按 CSV 行号对位（tag_id 列是 Danbooru 标签 ID，别用）
                    cat = int(row["category"])
                    if cat == 4:
                        chars.append((idx, row["name"]))
                    elif cat == 3:
                        copies.append((idx, row["name"]))

            _session = (sess, chars, copies, size)
            log.info(
                "WD14 打标已启用：%s（输入 %dpx，角色标签 %d 个）", d, size, len(chars)
            )
        except Exception as exc:  # noqa: BLE001
            _failed = True
            log.warning("WD14 打标加载失败，已禁用：%s", exc)
            return False
    return True


def _preprocess(data: bytes, size: int):
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGBA")
    # 合成到白底（透明 PNG 直接贴黑底会毁掉打标）
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img = Image.alpha_composite(bg, img).convert("RGB")
    # 白边补方
    w, h = img.size
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2))
    canvas = canvas.resize((size, size))
    import numpy as np

    arr = np.asarray(canvas, dtype=np.float32)
    return arr[None]  # NHWC：[1, size, size, 3]，通道顺序由 _flip_bgr 控制

_BGR = True  # 官方示例做 RGB->BGR 翻转；实测见 _test_tagger


def _flip_if_bgr(arr):
    if not _BGR:
        return arr
    import numpy as np

    return arr[:, :, :, ::-1]


def tag_image_sync(data: bytes) -> tuple[list[str], list[str]]:
    """同步打标，返回 (character 标签, copyright 标签)。未加载/失败返回空对。"""
    if not available() or not _load():
        return [], []
    sess, chars, copies, size = _session

    x = _flip_if_bgr(_preprocess(data, size))
    preds = sess.run(None, {sess.get_inputs()[0].name: x})[0].reshape(-1)
    hit_chars = [name for tid, name in chars if float(preds[tid]) >= CHAR_THRESHOLD]
    hit_copies = [
        name for tid, name in copies if float(preds[tid]) >= COPYRIGHT_THRESHOLD
    ]
    return hit_chars[:MAX_TAGS], hit_copies[:MAX_TAGS]


async def tag_image(data: bytes) -> tuple[list[str], list[str]]:
    """线程池里跑推理；任何失败返回空对——打标是加分项，不能拖垮看图。"""
    try:
        return await asyncio.to_thread(tag_image_sync, data)
    except Exception as exc:  # noqa: BLE001
        log.warning("WD14 打标失败：%s", exc)
        return [], []
