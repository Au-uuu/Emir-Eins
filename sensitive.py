"""
键政类内容过滤：命中即**静默不回复**。

为什么需要
----------
QQ 群聊里机器人一旦接话到政治话题，轻则被举报、重则封号。模型（通义千问）
自身有内容安全，但多一道本地过滤成本极低，能显著降低风险。

词表
----
默认读 `data/sensitive_words.txt`（每行一个词，`#` 开头为注释）。
该文件由 `deploy/fetch_sensitive_words.sh` 从开源词库
[`konsheng/Sensitive-lexicon`](https://github.com/konsheng/Sensitive-lexicon)（MIT）
的「政治/反动/贪腐/暴恐」四个分类合并生成。

⚠️ 词表里含「政府」「中央领导」这类**日常词**，会误杀正常聊天。
   想放行某些词，把它们写进 `data/sensitive_allow.txt`（每行一个，构建时会被剔除）。

匹配策略
--------
- **防绕过**：先归一化——全角转半角、转小写、去掉空白与常见分隔符，
  这样「政 府」「政*府」「ＺＦ」都能命中。
- **快**：优先用 `pyahocorasick`（C 扩展，多模式一次扫描）；
  装不上就退回纯 Python 子串匹配（词表几千条时也就 1ms 量级）。
- **稳**：词表缺失或为空时，过滤器自动禁用，绝不影响机器人启动。
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("qqbot.sensitive")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_WORDS_PATH = os.path.join(BASE_DIR, "data", "sensitive_words.txt")

# 单字词太容易误杀（例如「党」会命中「党员」「政党」等正常词），一概不进词表
MIN_WORD_LEN = 2

# 归一化时要剔除的字符：空白与常见「加料」分隔符
_STRIP_CHARS = " \t\r\n\u3000·*.-_+|/\\~#$%^&()[]{}<>《》「」【】" \
               "，。、！？!?,.;:'\"“”‘’…—–"

# 全角 -> 半角。
# ⚠️ str.translate() 的映射字典必须用**字符序号(int)**做键；用 chr() 生成字符串键
# 是不生效的（这个坑踩过一次）。
_FULLWIDTH = {0xFF01 + i: chr(0x21 + i) for i in range(94)}  # ！-～ -> !-~
_FULLWIDTH[0x3000] = " "  # 全角空格

# 预先算好剔除表，避免每次调用都重建
_STRIP_TABLE = {ord(c): None for c in _STRIP_CHARS}


def normalize(text: str) -> str:
    """归一化：全角转半角、转小写、去掉空白与常见分隔符。"""
    return (text or "").translate(_FULLWIDTH).lower().translate(_STRIP_TABLE)


class WordFilter:
    """词表过滤器。`hit()` 返回命中的词（便于日志排查），未命中返回 None。"""

    def __init__(self, words_path: str | None = None):
        self._explicit_path = words_path
        self._words: list[str] = []
        self._automaton = None
        self._loaded = False

    @property
    def words_path(self) -> str:
        """词表路径。惰性读取环境变量——`bot.py` 的 load_dotenv() 在 import 之后才跑。"""
        if self._explicit_path:
            return self._explicit_path
        return os.getenv("QQ_BOT_SENSITIVE_WORDS", DEFAULT_WORDS_PATH) \
            or DEFAULT_WORDS_PATH

    # ---------------- 加载 ----------------

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        path = self.words_path
        if not path or not os.path.isfile(path):
            log.warning("敏感词表不存在，过滤已禁用：%s", path)
            return

        seen: set[str] = set()
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                # 注释判断要在 normalize 之前 —— normalize 会把「#」当分隔符剥掉，
                # 之后 startswith("#") 永远不成立，注释行会被当成词收进词表。
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                word = normalize(line)
                if len(word) < MIN_WORD_LEN or word in seen:
                    continue
                seen.add(word)
                self._words.append(word)

        if not self._words:
            log.warning("敏感词表为空，过滤已禁用：%s", path)
            return

        self._build_automaton()
        log.info(
            "敏感词过滤已启用：%d 条，引擎=%s",
            len(self._words),
            "ahocorasick" if self._automaton is not None else "python",
        )

    def _build_automaton(self) -> None:
        try:
            import ahocorasick  # type: ignore[import-not-found]
        except ImportError:
            return
        try:
            automaton = ahocorasick.Automaton()
            for word in self._words:
                automaton.add_word(word, word)
            automaton.make_automaton()
            self._automaton = automaton
        except Exception as exc:  # noqa: BLE001
            log.warning("构建 ahocorasick 失败，退回纯 Python 匹配：%s", exc)

    # ---------------- 匹配 ----------------

    @property
    def enabled(self) -> bool:
        self._load()
        return bool(self._words)

    @property
    def size(self) -> int:
        self._load()
        return len(self._words)

    def hit(self, text: str) -> str | None:
        """命中则返回命中的那个词，否则 None。"""
        self._load()
        if not self._words:
            return None
        flat = normalize(text)
        if not flat:
            return None

        if self._automaton is not None:
            for _, word in self._automaton.iter(flat):
                return word
            return None

        for word in self._words:
            if word in flat:
                return word
        return None


# 全局单例：首次使用时才真正读盘，避免 import 阶段就有 I/O
filter = WordFilter()
