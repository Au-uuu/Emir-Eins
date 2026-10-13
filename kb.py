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
_HEADING_RE = re.compile(r"^#{2,}\s*(.+)$", re.M)
# 匹配名字时要去掉的标点/空白（「：」「·」这类会出现在名字里）
_PUNCT_RE = re.compile(r"[\s：:·・,，。.！!？?「」『』（）()【】\[\]《》<>—\-~/]")

# 既是词条名、又是日常词：**单独出现时不许触发检索**，只有同时提到作品名才认。
# 名单故意保持很短——只有确实会误伤的才放进来（"满月"实测误伤过一次：
# 用户说「今日运势」时它被拽进来）。像"流萤"这种虽然也是常见词，但用户更可能
# 是在问那个角色，所以不放进来；以后发现误伤再往里加。
_GENERIC_NAMES = {
    # 兼作日常词的词条名（"满月"实测误伤过一次：用户说「今日运势」时它被拽进来）
    "满月", "月亮", "星星", "天气", "运势", "运气",
    # 章节名/泛称，不是实体名，不该用来触发检索
    "角色", "设定", "世界观", "作品", "游戏", "介绍", "简介", "概述",
    "一览", "列表", "说明", "玩法", "剧情", "背景", "技能", "面板",
    "档案", "语音", "皮肤", "干员", "地图", "武器", "道具", "系统",
}

_lock = threading.Lock()
_loaded = False
_chunks: list[dict] = []
_df: dict[str, int] = {}
_avgdl = 0.0
_n = 0
# 作品 → (作品名集合, 词条名集合)。为空表示这份语料没有标题结构，就不做「必须提到」门槛。
_names_by_work: dict[str, tuple[set[str], set[str]]] = {}


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


def _normalize(text: str) -> str:
    """去掉标点/空白并小写，便于做包含匹配（名字里常有「：」「·」）。"""
    return _PUNCT_RE.sub("", (text or "").lower())


def _works_mentioned(query: str) -> set[str] | None:
    """query 提到了哪些作品/词条；None 表示不做门槛（语料无标题结构）。

    这是为了挡住「今日运势」这类**日常泛词**：BM25 只看词形，会把《蔚蓝档案》的
    「满月（四运势神）」、《崩坏星穹铁道》的「开拓者（星穹列车乘员）」整段塞给
    模型，模型顺着编出跟话题无关的游戏设定（实测过一次）。
    规则：只要 query 里出现了作品名，或某个词条名（`_GENERIC_NAMES` 里那些兼作
    日常词的除外），才认为用户真的在问知识库里的东西。
    """
    if not _names_by_work:
        return None
    q = _normalize(query)
    if not q:
        return set()
    q_toks = [t for t in _tokenize(q) if len(t) >= 2]
    hit: set[str] = set()
    for work, (titles, entries) in _names_by_work.items():
        # 作品名：允许「部分提及」——用户常说"星穹铁道"而不是全名"崩坏星穹铁道"
        if any(n and (n in q or any(t in n for t in q_toks)) for n in titles):
            hit.add(work)
            continue
        # 词条名：必须在句子里**整段出现**（否则"角色""设定"这类泛词会误触发）
        if any(n and n not in _GENERIC_NAMES and n in q for n in entries):
            hit.add(work)
    return hit


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
        names_by_work: dict[str, tuple[set[str], set[str]]] = {}
        for path in files:
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                log.warning("读取知识库文件失败：%s（%s）", path, exc)
                continue
            work = os.path.splitext(os.path.basename(path))[0]
            titles: set[str] = set()
            if len(work.strip()) >= 2:      # 文件名也要求 2 字以上（同词条名的理由）
                titles.add(_normalize(work))
            entries: set[str] = set()
            for i, chunk in enumerate(_split_chunks(text)):
                toks = _tokenize(chunk)
                if not toks:
                    continue
                tf: dict[str, int] = {}
                for t in toks:
                    tf[t] = tf.get(t, 0) + 1
                # 段落开头的 `## XXX` 是词条名；「（作品/世界观）」那条算作品名
                m = _HEADING_RE.match(chunk)
                raw = m.group(1).splitlines()[0].strip() if m else ""
                short = raw.split("（")[0].split("(")[0].strip()
                # ⚠️ 只收 2 个字以上的名字：单字词条（鸣潮有个「角」）会被
                # 「角色」这类常见词包含，导致门槛形同虚设（踩过）
                if len(short) >= 2:
                    if "作品" in raw or "世界观" in raw:
                        titles.add(_normalize(short))
                    else:
                        entries.add(_normalize(short))
                chunks.append(
                    {
                        "text": chunk,
                        "source": f"{os.path.basename(path)}#{i + 1}",
                        "tf": tf,
                        "len": len(toks),
                        "work": work,
                    }
                )
                for t in tf:
                    df[t] = df.get(t, 0) + 1
            names_by_work[work] = (titles, entries)
        if not chunks:
            log.info("知识库没有可用内容（%s），RAG 跳过", directory)
            return
        _chunks = chunks
        _df = df
        _n = len(chunks)
        _avgdl = sum(c["len"] for c in chunks) / _n
        # 只有语料真的带 `## 标题` 结构时才启用「必须提到」门槛；否则维持旧行为
        _names_by_work.clear()
        if any(e for _, e in names_by_work.values()):
            _names_by_work.update(names_by_work)
        log.info("知识库已加载：%d 段（%s，门槛%s）", _n, directory,
                 "开" if _names_by_work else "关")


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
    """返回 [(分数, 文本, 来源)]，按分数降序，已过滤低于阈值者。

    先过「必须提到作品/角色名」的门槛（见 `_works_mentioned`）：日常泛词
    （运势、运气、天气…）不再把无关游戏设定拽进来；提到某作品时也只在
    该作品的词条里检索，避免跨作品串味。
    """
    _load()
    if not _chunks:
        return []
    allowed = _works_mentioned(query)
    if allowed is not None and not allowed:
        log.info("[知识库] 未提到已知作品/角色，跳过检索（query=%.20s）", query)
        return []
    qtoks = _tokenize(query)
    if not qtoks:
        return []
    pool = _chunks if allowed is None else [c for c in _chunks if c.get("work") in allowed]
    if not pool:
        return []
    scored = [(_bm25(qtoks, c), c) for c in pool]
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
