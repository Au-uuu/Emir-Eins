"""离线动漫打标：多模型注册表 + 本地 ONNX 推理，给看图链路提供「识别线索」。

设计（借鉴 nestflow/ComfyUI-Booru-Tagger 的注册表思路）
-------------------------------------------------------
每种模型在 `MODELS` 里登记：目录、ONNX 文件名、标签元数据文件名、预处理家族、
默认阈值。用 `QQ_BOT_TAGGER_MODEL` 选择要加载的模型（默认 `camie`）。
**同一时刻只加载一个模型**——服务器内存紧张（3.6G，bot 常驻约 1G），多模型同时
驻留会 OOM；所以选择在启动读环境变量时定死，不在运行时热切换。

已登记模型
----------
- `camie`（默认）：Camais03/camie-tagger-v2。512²，角色标签 2.6 万+、**含作品
  （copyright）标签**，许可证 GPL-3.0，免登录。
- `animetimm-swinv2`（可选，CPU 更快）：animetimm/swinv2_base_window8_256.dbv4-full。
  256²（像素只有 Camie 的 1/4），Danbooru v4 训练，标签表带**每标签 best_threshold**；
  只训练了 general/character/rating，**没有作品类**；仓库 GPL-3.0 且**需在 HF 网页
  同意条款后带 token 下载**。

模型文件（不进 git、不进 data/ 常规备份），放在 models/<注册表目录>/ 下：
  camie：camie-tagger-v2.onnx + camie-tagger-v2-metadata.json
    curl -L https://hf-mirror.com/Camais03/camie-tagger-v2/resolve/main/camie-tagger-v2.onnx
    curl -L https://hf-mirror.com/Camais03/camie-tagger-v2/resolve/main/camie-tagger-v2-metadata.json
  animetimm-swinv2：model.onnx + selected_tags.csv（+ 可选 preprocess.json）
    （gated：先登录 HF 同意条款，再 `hf download` / 带 token 下载）
文件缺失或加载失败 → 打标静默禁用，看图链路照常工作。

配置（.env，惰性读取——bot.py 的 load_dotenv() 晚于 import，import 期读只能读到空）
--------------------------------------------------------------------------------
| 变量 | 说明 |
|---|---|
| `QQ_BOT_TAGGER_MODEL` | 选哪个模型：`camie`（默认）/ `animetimm-swinv2` |
| `QQ_BOT_TAGGER_DIR` | 覆盖模型目录（相对/绝对），默认 `models/<注册表目录>` |
| `QQ_BOT_TAGGER_CHAR_THRESHOLD` | 角色标签阈值（默认按模型） |
| `QQ_BOT_TAGGER_COPYRIGHT_THRESHOLD` | 作品标签阈值（默认按模型） |
| `QQ_BOT_TAGGER_THRESHOLD` | 统一覆盖上面两个 |

职责边界：本模块只负责「图片字节 → (角色标签, 作品标签)」。线索怎么拼、怎么喂给
视觉模型在 `bot.py::tagger_hints`；打标失败静默，绝不拖垮看图。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import threading
from collections import OrderedDict

log = logging.getLogger("qqbot.tagger")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 标签最多各取几个（角色 / 作品）拼进线索
MAX_TAGS = 4

# 角色「候选」（软线索）：即使低于硬阈值，也按置信度取前几个一起喂给视觉模型。
# 实测正确角色常落在 0.4~0.5，硬阈值 0.5 经常一个角色都不给，模型只能瞎猜。
CANDIDATE_MAX = 3
CANDIDATE_FLOOR = 0.25

# 打标结果缓存（LRU）：key = 图片内容 sha256，value = (character, copyright) 标签。
# 条目只是短标签列表，内存开销可忽略；128 条足够覆盖群里反复转的那几十张图。
_CACHE_MAX = 128
_cache: "OrderedDict[str, tuple[list[str], list[str]]]" = OrderedDict()

# ---------------------------------------------------------------------------
# 模型注册表
#
# family 决定预处理与标签表解析方式：
#   camie      —— 直缩输入边长 + ImageNet 归一化 + NCHW；metadata 是 JSON
#   animetimm  —— PadToSize→Resize→CenterCrop→Normalize；metadata 是 selected_tags.csv
# char_threshold / copyright_threshold 是该模型的默认阈值，可被环境变量覆盖；
# 若标签表自带 best_threshold（animetimm），实际阈值取 max(best_threshold, 该默认)，
# 与 nestflow/ComfyUI-Booru-Tagger 的做法一致。
# ---------------------------------------------------------------------------
MODELS: dict[str, dict] = {
    "camie": {
        "dir": "camie-tagger-v2",
        "model": "camie-tagger-v2.onnx",
        "metadata": "camie-tagger-v2-metadata.json",
        "family": "camie",
        "char_threshold": 0.50,
        "copyright_threshold": 0.50,
    },
    "animetimm-swinv2": {
        "dir": "animetimm-swinv2_base-dbv4-full",
        "model": "model.onnx",
        "metadata": "selected_tags.csv",
        "preprocess": "preprocess.json",
        "family": "animetimm",
        # 官方推荐：general 0.41 / character 0.59（见模型卡）
        "char_threshold": 0.59,
        "copyright_threshold": 0.50,  # 该模型无作品类，占位
        # preprocess.json 缺失（例如手动下载漏了）时的兜底：swinv2_base 输入 256²
        "fallback_preprocess": {
            "pad": [256, 256],
            "resize": 256,
            "crop": [256, 256],
            "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225],
        },
    },
}

DEFAULT_MODEL = "camie"

_lock = threading.Lock()
# (session, input_name, output_name, prep, char_entries, copy_entries, char_thr, copy_thr)
# char_entries / copy_entries：[(输出下标, 标签名, best_threshold 或 None), ...]
_session = None
_failed = False


# ---------------------------------------------------------------------------
# 配置（惰性读取）
# ---------------------------------------------------------------------------
def _selected_model() -> str:
    name = (os.getenv("QQ_BOT_TAGGER_MODEL") or "").strip() or DEFAULT_MODEL
    if name not in MODELS:
        log.warning("未知打标模型 %r，回退到 %s", name, DEFAULT_MODEL)
        return DEFAULT_MODEL
    return name


def _entry() -> dict:
    return MODELS[_selected_model()]


def model_dir() -> str:
    return os.getenv("QQ_BOT_TAGGER_DIR", "") or os.path.join(
        BASE_DIR, "models", _entry()["dir"]
    )


def _model_path() -> str:
    return os.path.join(model_dir(), _entry()["model"])


def _metadata_path() -> str:
    return os.path.join(model_dir(), _entry()["metadata"])


def _preprocess_path() -> str:
    filename = _entry().get("preprocess")
    return os.path.join(model_dir(), filename) if filename else ""


def _env_threshold(name: str, default: float) -> float:
    """读阈值环境变量；专用变量优先，其次统一的 QQ_BOT_TAGGER_THRESHOLD。"""
    for key in (name, "QQ_BOT_TAGGER_THRESHOLD"):
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        try:
            return float(raw)
        except ValueError:
            log.warning("%s=%r 不是合法数字，忽略", key, raw)
    return default


def available() -> bool:
    """模型文件齐全才可用（缺文件标记禁用，避免每张图都去 stat）。"""
    global _failed
    if _failed:
        return False
    ok = os.path.isfile(_model_path()) and os.path.isfile(_metadata_path())
    if not ok:
        _failed = True
        log.info(
            "打标模型文件缺失（%s，模型=%s），角色打标禁用",
            model_dir(),
            _selected_model(),
        )
    return ok


# ---------------------------------------------------------------------------
# 标签表解析
# ---------------------------------------------------------------------------
def _parse_camie_tags(meta: dict) -> tuple[list[str], dict]:
    """
    解析 Camie metadata，返回 (按输出行号排列的标签名, tag->category)。

    兼容 `idx_to_tag`（行号→名）与 `tag_to_idx`（名→行号）两种写法，
    缺行号处补空串占位，保证下标与模型输出向量对齐。
    """
    mapping = (meta.get("dataset_info") or {}).get("tag_mapping") or {}
    if "idx_to_tag" in mapping:
        idx_to_tag = {int(k): v for k, v in (mapping.get("idx_to_tag") or {}).items()}
        n = (max(idx_to_tag) + 1) if idx_to_tag else 0
        tags = [idx_to_tag.get(i, "") for i in range(n)]
    else:
        tag_to_idx = mapping.get("tag_to_idx") or {}
        n = (max(int(v) for v in tag_to_idx.values()) + 1) if tag_to_idx else 0
        tags = [""] * n
        for tag, i in tag_to_idx.items():
            tags[int(i)] = tag
    return tags, (mapping.get("tag_to_category") or {})


def _parse_animetimm_rows(path: str) -> list[tuple[str, str, float | None]]:
    """
    解析 AnimeTimm 的 selected_tags.csv，返回 [(category, name, best_threshold), ...]。

    输出行号 = 列表下标（CSV 行序即模型输出向量的对位顺序）。
    category 是数字字符串：0=general、4=character、9=rating（dbv4-full 无作品类）。
    best_threshold 列可能缺失或为空，此时该标签的 best_threshold 为 None。
    """
    import csv

    rows: list[tuple[str, str, float | None]] = []
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            category = str(row.get("category", "")).strip()
            raw_best = row.get("best_threshold")
            best: float | None = None
            if raw_best not in (None, ""):
                try:
                    best = float(raw_best)
                except ValueError:
                    best = None
            rows.append((category, name, best))
    return rows


def _load_animetimm_preprocess(path: str, fallback: dict) -> dict:
    """
    读模型自带的 preprocess.json（DeepGHS 导出的 torchvision Compose），
    抽出 pad / resize / crop / mean / std；解析失败或文件缺失时退回 fallback。
    """
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                steps = json.load(fh)["test"]
            pad = next(s for s in steps if s["type"] == "pad_to_size")["size"]
            resize = next(s for s in steps if s["type"] == "resize")["size"]
            crop = next(s for s in steps if s["type"] == "center_crop")["size"]
            norm = next(s for s in steps if s["type"] == "normalize")
            return {
                "pad": [int(pad[0]), int(pad[1])],
                "resize": resize,
                "crop": [int(crop[0]), int(crop[1])],
                "mean": [float(v) for v in norm["mean"]],
                "std": [float(v) for v in norm["std"]],
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("解析 preprocess.json 失败，改用兜底参数：%s", exc)
    return fallback


# ---------------------------------------------------------------------------
# 预处理
# ---------------------------------------------------------------------------
def _to_rgb(data: bytes):
    """解码图片并合成到白底（透明 PNG 直接贴黑底会毁掉打标）。"""
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.alpha_composite(bg, img).convert("RGB")


def _preprocess_camie(data: bytes, size: int):
    """Camie：直缩到输入边长（LANCZOS）+ ImageNet 归一化 + NCHW。"""
    import numpy as np
    from PIL import Image

    img = _to_rgb(data).resize((size, size), Image.LANCZOS)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    arr = (arr - mean) / std
    return np.transpose(arr, (2, 0, 1))[None]


def _resize_hw(h: int, w: int, size) -> tuple[int, int]:
    """torchvision Resize 语义：整数按短边缩放，二元组是精确 (h, w)。"""
    if isinstance(size, (list, tuple)):
        if len(size) == 1:
            size = size[0]
        else:
            return int(size[0]), int(size[1])
    target = int(size)
    if h <= w:
        return target, max(1, round(target * w / h))
    return max(1, round(target * h / w)), target


def _preprocess_animetimm(data: bytes, ctx: dict):
    """
    AnimeTimm：PadToSize（等比缩放到「装得进 pad 尺寸」再居中白边）→ Resize →
    CenterCrop → ImageNet 归一化 → NCHW。与 DeepGHS 的 preprocess.json 一致。
    """
    import numpy as np
    from PIL import Image

    img = _to_rgb(data)
    pw, ph = ctx["pad"]  # imgutils 的 size 是 (width, height)
    w, h = img.size
    # PadToSize：先等比缩放到装得进 (pw, ph)，再居中补白
    ratio = min(pw / w, ph / h)
    nw, nh = max(1, round(w * ratio)), max(1, round(h * ratio))
    if (nw, nh) != (w, h):
        img = img.resize((nw, nh), Image.BILINEAR)
    canvas = Image.new("RGB", (pw, ph), (255, 255, 255))
    canvas.paste(img, ((pw - nw) // 2, (ph - nh) // 2))

    # Resize（对正方形补边而言就是缩到 crop 尺寸）
    rh, rw = _resize_hw(ph, pw, ctx["resize"])
    if (rw, rh) != (pw, ph):
        canvas = canvas.resize((rw, rh), Image.BICUBIC)

    # CenterCrop：torchvision 的裁剪起点四舍五入
    ch, cw = ctx["crop"]
    top = round((rh - ch) / 2)
    left = round((rw - cw) / 2)
    if rh >= ch and rw >= cw:
        canvas = canvas.crop((left, top, left + cw, top + ch))

    arr = np.asarray(canvas, dtype=np.float32) / 255.0
    mean = np.array(ctx["mean"], dtype=np.float32)
    std = np.array(ctx["std"], dtype=np.float32)
    arr = (arr - mean) / std
    return np.transpose(arr, (2, 0, 1))[None]


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------
def _load():
    global _session, _failed
    if _session is not None or _failed:
        return _session is not None
    with _lock:
        if _session is not None or _failed:
            return _session is not None
        try:
            import onnxruntime as ort

            entry = _entry()
            family = entry["family"]

            so = ort.SessionOptions()
            so.intra_op_num_threads = 2  # 4 核小机，给主进程留核
            sess = ort.InferenceSession(
                _model_path(),
                sess_options=so,
                providers=["CPUExecutionProvider"],
            )
            input_name = sess.get_inputs()[0].name
            output_name = sess.get_outputs()[0].name

            if family == "camie":
                with open(_metadata_path(), encoding="utf-8") as fh:
                    meta = json.load(fh)
                size = int((meta.get("model_info") or {}).get("img_size") or 0)
                if size <= 0:
                    shape = sess.get_inputs()[0].shape
                    size = int(shape[2]) if len(shape) > 2 and isinstance(shape[2], int) else 512
                tags, tag_to_cat = _parse_camie_tags(meta)

                def category_of(tag: str) -> str:
                    return str(tag_to_cat.get(tag, "")).strip().lower()

                char_entries = [
                    (i, t, None)
                    for i, t in enumerate(tags)
                    if t and category_of(t) == "character"
                ]
                copy_entries = [
                    (i, t, None)
                    for i, t in enumerate(tags)
                    if t and category_of(t) == "copyright"
                ]
                prep = lambda data: _preprocess_camie(data, size)  # noqa: E731
                shape_note = f"输入 {size}px"

            else:  # animetimm
                rows = _parse_animetimm_rows(_metadata_path())
                # category：0=general、4=character、9=rating；dbv4-full 无作品类
                char_entries = [
                    (i, name, best)
                    for i, (cat, name, best) in enumerate(rows)
                    if cat == "4"
                ]
                copy_entries = [
                    (i, name, best)
                    for i, (cat, name, best) in enumerate(rows)
                    if cat == "3"  # 预留：若换到含作品标签的 csv
                ]
                ctx = _load_animetimm_preprocess(
                    _preprocess_path(), entry["fallback_preprocess"]
                )
                prep = lambda data: _preprocess_animetimm(data, ctx)  # noqa: E731
                shape_note = (
                    f"输入 {ctx['crop'][0]}px，"
                    f"{'带' if char_entries and char_entries[0][2] is not None else '无'}"
                    "每标签 best_threshold"
                )

            if not char_entries:
                log.warning("打标标签表里没有解析到 character 标签，请核对元数据格式")

            char_thr = _env_threshold(
                "QQ_BOT_TAGGER_CHAR_THRESHOLD", entry["char_threshold"]
            )
            copy_thr = _env_threshold(
                "QQ_BOT_TAGGER_COPYRIGHT_THRESHOLD", entry["copyright_threshold"]
            )
            _session = (
                sess,
                input_name,
                output_name,
                prep,
                char_entries,
                copy_entries,
                char_thr,
                copy_thr,
            )
            log.info(
                "打标已启用：模型=%s 目录=%s（%s，角色标签 %d 个，作品标签 %d 个；"
                "阈值 角色=%.2f 作品=%.2f）",
                _selected_model(),
                model_dir(),
                shape_note,
                len(char_entries),
                len(copy_entries),
                char_thr,
                copy_thr,
            )
        except Exception as exc:  # noqa: BLE001
            _failed = True
            log.warning("打标加载失败，已禁用：%s", exc)
            return False
    return True


# ---------------------------------------------------------------------------
# 推理
# ---------------------------------------------------------------------------
def _select(probs: list[float], entries: list, base_thr: float) -> list[str]:
    """
    从概率向量里选出命中标签（按置信度降序，最多 MAX_TAGS 个）。

    实际阈值 = max(每标签 best_threshold, 类别默认阈值)；没有 best_threshold 时
    只用类别默认阈值。纯 Python 实现（不依赖 numpy），便于本地测试。
    """
    hits: list[tuple[float, str]] = []
    for idx, name, best in entries:
        thr = base_thr if best is None else max(best, base_thr)
        if probs[idx] >= thr:
            hits.append((probs[idx], name))
    hits.sort(reverse=True)
    return [name for _, name in hits[:MAX_TAGS]]


def _candidates(
    probs: list[float],
    entries: list,
    n: int = CANDIDATE_MAX,
    floor: float = CANDIDATE_FLOOR,
) -> list[tuple[str, float]]:
    """返回置信度 >= floor 的前 n 个角色候选 [(名字, 置信度)]（不管是否过硬阈值）。"""
    hits = [(probs[idx], name) for idx, name, _ in entries if probs[idx] >= floor]
    hits.sort(reverse=True)
    return [(name, score) for score, name in hits[:n]]


def _infer(data: bytes) -> tuple[list[str], list[str], list]:
    """核心推理，返回 (character, copyright, 角色候选[(name, prob)])。"""
    if not available() or not _load():
        return [], [], []
    sess, input_name, output_name, prep, char_entries, copy_entries, char_thr, copy_thr = _session

    import numpy as np

    x = prep(data)
    logits = sess.run([output_name], {input_name: x})[0].reshape(-1)
    probs = (1.0 / (1.0 + np.exp(-logits.astype(np.float32)))).tolist()
    return (
        _select(probs, char_entries, char_thr),
        _select(probs, copy_entries, copy_thr),
        _candidates(probs, char_entries),
    )


def tag_image_sync(data: bytes) -> tuple[list[str], list[str]]:
    """同步打标，返回 (character 标签, copyright 标签)。未加载/失败返回空对。"""
    chars, copies, _ = _infer(data)
    return chars, copies


def tag_image_detail_sync(data: bytes) -> tuple[list[str], list[str], list]:
    """同步打标（详细版），返回 (character, copyright, 角色候选[(name, prob)])。"""
    return _infer(data)


def _fmt_tags(result: tuple[list[str], list[str]]) -> str:
    """把打标结果拼成一行给人看的日志。"""
    chars, copies = result
    if not chars and not copies:
        return "无标签命中"
    parts = []
    if copies:
        parts.append("作品=" + ",".join(copies))
    if chars:
        parts.append("角色=" + ",".join(chars))
    return "；".join(parts)


def _fmt_candidates(cands: list) -> str:
    return "、".join(f"{n}({s:.2f})" for n, s in cands)


async def _tag_async(data: bytes) -> tuple[list[str], list[str], list]:
    """
    线程池里跑推理；任何失败返回空三元组——打标是加分项，不能拖垮看图。

    结果按**图片内容哈希**缓存：同一张图重复出现（群里转同一张立绘/梗图很常见）
    直接复用。只缓存**真的跑过推理**的结果——模型缺失时结果恒为空三元组。
    """
    key = hashlib.sha256(data).hexdigest()
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
        log.info("[打标] 缓存命中：%s", _fmt_tags(hit[:2]))
        return hit

    try:
        result = await asyncio.to_thread(tag_image_detail_sync, data)
    except Exception as exc:  # noqa: BLE001
        log.warning("打标失败：%s", exc)
        return [], [], []

    if _session is not None:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
        # 打标结果必须可观测：否则「没认出角色」到底是模型不认、还是压根没命中标签，
        # 从日志上完全看不出来（2026-10-08 排查时卡在这里）。没命中角色时把候选也打出来。
        chars, copies, cands = result
        line = _fmt_tags((chars, copies))
        if not chars and cands:
            line += "；角色候选=" + _fmt_candidates(cands)
        log.info("[打标] %s", line)
    return result


async def tag_image(data: bytes) -> tuple[list[str], list[str]]:
    """兼容旧调用：只返回 (角色, 作品)。"""
    chars, copies, _ = await _tag_async(data)
    return chars, copies


async def tag_image_detail(data: bytes) -> tuple[list[str], list[str], list]:
    """返回 (角色, 作品, 角色候选[(name, score)])。"""
    return await _tag_async(data)
