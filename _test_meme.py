# -*- coding: utf-8 -*-
"""梗库测试（不联网，模型与搜索都用假数据）。"""

import asyncio
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="qqbot_meme_")
os.environ["QQ_BOT_MEME"] = "1"
os.environ["QQ_BOT_MEME_SEARCH"] = "1"
os.environ["QQ_BOT_MEME_CONF"] = "0.5"
os.environ["QQ_BOT_MEME_DB"] = os.path.join(TMP, "meme.db")
os.environ["QQ_BOT_WEBSEARCH"] = "1"

import meme  # noqa: E402
import persona  # noqa: E402
import websearch  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


def test_question_term():
    print("[1] 从提问里取词")
    cases = [
        ("班味是什么梗", "班味"),
        ("「绷不住了」是什么意思", "绷不住了"),
        ("请问 yyds 是什么梗", "yyds"),
        ("查一下 尊嘟假嘟", "尊嘟假嘟"),
        ("这是什么梗", ""),
        ("你什么意思", ""),
        ("今天天气不错", ""),
    ]
    for text, want in cases:
        got = meme.question_term(text)
        check(f"{text} -> {want or '空'}", got == want, got)


def test_manual_and_render():
    print("[2] 手写 / 注入")
    ok = asyncio.run(meme.add_manual("班味", "打工人长期上班后带出的疲惫气质"))
    check("手写入库", ok)
    row = asyncio.run(meme.get("班味"))
    check("能查到手写条目", bool(row) and row["source"] == "manual", row)
    block = meme.render("今天班味太重了")
    check("聊天里能注入", "【梗库资料】" in block and "疲惫气质" in block, block)
    check("短词不误命中", meme.render("班") == "")
    rows = asyncio.run(meme.list_recent(10))
    check("列表包含词条", any(r["term"] == "班味" for r in rows), rows)


def test_research():
    print("[3] 联网查梗 + 入库")
    calls = {"search": 0, "raw": 0}
    summary = "「尊嘟假嘟」是“真的假的”的可爱谐音，源自网络模仿口齿不清的说话方式。"

    def fake_search(query):
        calls["search"] += 1
        return summary

    async def fake_raw(prompt, text, session="", kind="learn"):
        calls["raw"] += 1
        data = {
            "term": "尊嘟假嘟",
            "meaning": "“真的假的”的可爱谐音",
            "origin": "网络模仿口齿不清的说话方式",
            "usage": "表示惊讶或怀疑",
            "aliases": ["尊嘟", "假嘟"],
            "confidence": 0.82,
        }
        return json.dumps(data, ensure_ascii=False)

    orig_search, orig_raw = websearch.search, persona.raw_complete
    websearch.search, persona.raw_complete = fake_search, fake_raw
    try:
        entry = asyncio.run(meme.research("尊嘟假嘟"))
        check("查到并返回", bool(entry) and "谐音" in entry["meaning"], entry)
        row = asyncio.run(meme.get("尊嘟假嘟"))
        check("已入库", bool(row) and row["source"] == "web", row)
        check("别名也存了", "尊嘟" in meme.render("他刚才说尊嘟"), meme.render("他刚才说尊嘟"))
        entry2 = asyncio.run(meme.research("尊嘟假嘟"))
        check("第二次走本地", bool(entry2) and calls["search"] == 1, calls)

        # 手写条目不被自动结果覆盖
        asyncio.run(meme.add_manual("尊嘟假嘟", "手写解释，不许覆盖"))
        asyncio.run(meme.research("尊嘟假嘟", force=True))
        row2 = asyncio.run(meme.get("尊嘟假嘟"))
        check("手写优先", row2["meaning"] == "手写解释，不许覆盖", row2)
    finally:
        websearch.search, persona.raw_complete = orig_search, orig_raw


def test_reject_cache():
    print("[4] 拒绝缓存")
    calls = {"search": 0}

    def fake_search(query):
        calls["search"] += 1
        return "这是一段没有明确梗含义的普通解释。"

    async def fake_raw(prompt, text, session="", kind="learn"):
        return json.dumps(
            {"term": "普通词", "meaning": "", "confidence": 0.1},
            ensure_ascii=False,
        )

    orig_search, orig_raw = websearch.search, persona.raw_complete
    websearch.search, persona.raw_complete = fake_search, fake_raw
    meme._last_research = 0
    meme._cooldown.clear()
    try:
        entry = asyncio.run(meme.research("普通词"))
        check("低置信度不入库", entry is None)
        check("进拒绝缓存", asyncio.run(meme.is_rejected("普通词")))
        meme._last_research = 0
        meme._cooldown.clear()
        asyncio.run(meme.research("普通词"))
        check("拒绝后不再联网", calls["search"] == 1, calls)
    finally:
        websearch.search, persona.raw_complete = orig_search, orig_raw


def test_learn_from_summary():
    print("[5] 从对话摘要学梗")
    summary = "「绝绝子」是表示“太绝了/太厉害了”的网络用语，常带夸张语气。"

    async def fake_raw(prompt, text, session="", kind="learn"):
        return json.dumps(
            {
                "term": "绝绝子",
                "meaning": "太绝了、太厉害了",
                "origin": "网络夸张表达",
                "usage": "夸人或自嘲",
                "aliases": [],
                "confidence": 0.8,
            },
            ensure_ascii=False,
        )

    orig_raw = persona.raw_complete
    persona.raw_complete = fake_raw
    try:
        entry = asyncio.run(meme.learn_from_summary("绝绝子是什么梗", summary))
        check("对话学习入库", bool(entry) and entry["term"] == "绝绝子", entry)
        check("之后能注入", "太绝了" in meme.render("这也太绝绝子了"))
    finally:
        persona.raw_complete = orig_raw

    print("[6] 删除")
    ok = asyncio.run(meme.delete("绝绝子"))
    check("能删除", ok and asyncio.run(meme.get("绝绝子")) is None)

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    test_question_term()
    test_manual_and_render()
    test_research()
    test_reject_cache()
    test_learn_from_summary()
    sys.exit(1 if failures else 0)
