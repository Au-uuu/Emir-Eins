"""本地知识库检索（轻量 BM25，零第三方依赖）。

用途
----
把「机器人应该知道的事实」放进 `data/kb/*.md|*.txt`，用户发消息时检索最相关的
几段，拼进提示词，让回复的事实更准（人设语气仍由角色卡负责）。

为什么不用向量检索
------------------
服务器是 4 核 / 3.6G 的 CPU 小机，向量检索要再常驻一个 embedding 模型，内存吃紧。
BM25 纯 Python，索引几千段是毫秒级，几乎不吃内存，够用。

中文怎么分词
------------
不引入 jieba：中文按**字符二元组**（bigram）切，英文/数字按单词切。
例如「终末地的佩丽卡」→ 终末/末地/地的/的佩/佩丽/丽卡。
这样无需分词器也能较好地匹配中文。

配置（.env，惰性读取）
----------------------
| 变量 | 说明 |
|---|---|
| `QQ_BOT_KB_DIR` | 知识库目录，默认 `data/kb`；不存在则整体跳过 |
| `QQ_BOT_KB_TOP` | 每次检索返回几段，默认 3 |
| `QQ_BOT_KB_MIN_SCORE` | 分数低于此值视为不相关，默认 1.5 |

改知识库文件后需重启进程才会重新加载（轻量起见不做热更）。
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading

log = logging.getLogger("qqbot.kb")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(BASE_DIR, "data", "kb")

# BM25 参数（经验值）
_K1 = 1.5
_B = 0.75
_MAX_CHUNK = 800  # 单段最大字数，过长再切

_ASCII_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

_lock = threading.Lock()
_loaded = False
_chunks: list[dict] = []
_df: dict[str, int] = {}
_avgdl = 0.0
_n = 0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def kb_dir() -> str:
    return os.getenv("QQ_BOT_KB_DIR", "") or DEFAULT_DIR


def topk() -> int:
    return max(1, _env_int("QQ_BOT_KB_TOP", 3))


def min_score() -> float:
    return _env_float("QQ_BOT_KB_MIN_SCORE", 1.5)


def _tokenize(text: str) -> list[str]:
    """英文/数字按词、中文按字符二元组切。"""
    low = (text or "").lower()
    tokens = _ASCII_RE.findall(low)
    for run in _CJK_RUN_RE.findall(low):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def _split_chunks(text: str) -> list[str]:
    """按空行切段；过长段落再按长度硬切。"""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        while len(p) > _MAX_CHUNK:
            out.append(p[:_MAX_CHUNK])
            p = p[_MAX_CHUNK:]
        if p:
            out.append(p)
    return out


def _load() -> None:
    global _loaded, _chunks, _df, _avgdl, _n
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        _loaded = True
        directory = kb_dir()
        if not os.path.isdir(directory):
            log.info("知识库目录不存在（%s），RAG 跳过", directory)
            return
        files = []
        for name in sorted(os.listdir(directory)):
            if name.startswith(("_", ".")):  # 说明/隐藏文件不计入检索
                continue
            if name.lower().endswith((".md", ".txt")):
                files.append(os.path.join(directory, name))
        if not files:
            log.info("知识库目录为空（%s），RAG 跳过", directory)
            return
        df: dict[str, int] = {}
        chunks: list[dict] = []
        for path in files:
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                log.warning("读取知识库文件失败：%s（%s）", path, exc)
                continue
            for i, chunk in enumerate(_split_chunks(text)):
                toks = _tokenize(chunk)
                if not toks:
                    continue
                tf: dict[str, int] = {}
                for t in toks:
                    tf[t] = tf.get(t, 0) + 1
                chunks.append(
                    {
                        "text": chunk,
                        "source": f"{os.path.basename(path)}#{i + 1}",
                        "tf": tf,
                        "len": len(toks),
                    }
                )
                for t in tf:
                    df[t] = df.get(t, 0) + 1
        if not chunks:
            log.info("知识库没有可用内容（%s），RAG 跳过", directory)
            return
        _chunks = chunks
        _df = df
        _n = len(chunks)
        _avgdl = sum(c["len"] for c in chunks) / _n
        log.info("知识库已加载：%d 段（%s）", _n, directory)


def _bm25(query_tokens: list[str], chunk: dict) -> float:
    score = 0.0
    dl = chunk["len"] or 1
    for token in set(query_tokens):
        df = _df.get(token)
        if not df:
            continue
        idf = math.log(1 + (_n - df + 0.5) / (df + 0.5))
        f = chunk["tf"].get(token, 0)
        if not f:
            continue
        score += idf * (f * (_K1 + 1)) / (f + _K1 * (1 - _B + _B * dl / _avgdl))
    return score


def search(query: str, limit: int | None = None) -> list[tuple[float, str, str]]:
    """返回 [(分数, 文本, 来源)]，按分数降序，已过滤低于阈值者。"""
    _load()
    if not _chunks:
        return []
    qtoks = _tokenize(query)
    if not qtoks:
        return []
    scored = [(_bm25(qtoks, c), c) for c in _chunks]
    scored.sort(key=lambda x: x[0], reverse=True)
    floor = min_score()
    out = []
    for s, c in scored[: (limit or topk())]:
        if s < floor:
            break
        out.append((s, c["text"], c["source"]))
    return out


def context(query: str) -> str:
    """把命中的知识拼成一段可注入提示词的文本；无命中返回空串。"""
    hits = search(query)
    if not hits:
        return ""
    blocks = [f"【{src}】{text}" for _, text, src in hits]
    log.info("[知识库] 命中 %d 段（query=%.20s）", len(hits), query)
    return "（本机知识库检索到以下资料，供你准确作答、语气仍按人设）：\n" + "\n".join(blocks) + "\n"
