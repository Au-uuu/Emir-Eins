"""联网搜索（两步式）：触发词、开关、解析、失败降级、注入包装。

零依赖、不联网：把 urllib.request.urlopen 换成假实现即可测。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import websearch  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


class _Resp:
    def __init__(self, payload: bytes) -> None:
        self._p = payload

    def read(self) -> bytes:
        return self._p

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


_last = {}


def _ok_urlopen(req, timeout=None):
    _last["req"] = req
    _last["body"] = json.loads(req.data.decode("utf-8"))
    return _Resp(json.dumps({"choices": [{"message": {"content": "检索摘要ABC"}}]}).encode())


def _boom_urlopen(req, timeout=None):
    raise OSError("网络挂了")


os.environ.pop("QQ_BOT_WEBSEARCH", None)
check("默认关闭：should_search 为假", websearch.should_search("今天有什么新闻") is False)
check("默认关闭：search 返回空", websearch.search("今天有什么新闻") == "")

os.environ["QQ_BOT_WEBSEARCH"] = "1"
os.environ["QQ_BOT_QWEN_KEY"] = "sk-test"
os.environ["QQ_BOT_QWEN_URL"] = "https://example.invalid/v1"
os.environ.pop("QQ_BOT_WEBSEARCH_TRIGGERS", None)

check("命中「今天/新闻」触发", websearch.should_search("今天有什么新闻") is True)
check("命中「最新」触发", websearch.should_search("看看最新消息") is True)
check("命中「多少钱」触发", websearch.should_search("这个多少钱") is True)
check("命中「发布」触发", websearch.should_search("新版本发布了吗") is True)
check("纯时间词闲聊不触发", websearch.should_search("你好呀，今天心情如何") is False)
check("「今天好累」不触发", websearch.should_search("今天好累啊") is False)
check("空串不触发", websearch.should_search("") is False)

websearch.urllib.request.urlopen = _ok_urlopen
out = websearch.search("今天有什么新闻")
check("解析出摘要", out == "检索摘要ABC", out)
check("请求带 enable_search", _last.get("body", {}).get("enable_search") is True)
check("请求模型默认 qwen-flash", _last.get("body", {}).get("model") == "qwen-flash")

wrapped = websearch.wrap(out)
check("wrap 含摘要", "检索摘要ABC" in wrapped)
check("wrap 含「联网检索」前缀", "联网检索" in wrapped)
check("wrap 要求保留名称数字", "照实保留" in wrapped)
check("空摘要 wrap 为空", websearch.wrap("") == "")

websearch.urllib.request.urlopen = _boom_urlopen
check("网络失败 → 空串降级", websearch.search("今天有什么新闻") == "")

# 自定义触发词：用环境变量覆盖，验证可配置
os.environ["QQ_BOT_WEBSEARCH_TRIGGERS"] = "开团"
check("自定义触发词命中", websearch.should_search("要不要开团") is True)
check("默认触发词被覆盖后不触发", websearch.should_search("今天有什么新闻") is False)

print(f"\n{'=' * 50}")
print(f"失败 {failures} 项")
sys.exit(1 if failures else 0)
