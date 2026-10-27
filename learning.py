"""
黑话 / 人物印象的自动学习。

原则
----
- 只用**本群最近消息**，不联网、不跨群；
- 只提取有明确上下文依据的内容，宁可少学；
- 自动条目 `source='auto'`，管理员可删；手动条目不会被自动覆盖；
- 每次最多 3 个黑话、3 个人物，控制 token 与误报；
- 默认低频：`QQ_BOT_MEMORY_LEARN_MINUTES=120`（0 = 关闭）。
"""

from __future__ import annotations

import json
import logging
import re
import time

import group_context
import memory_store
import persona
import sensitive

log = logging.getLogger("qqbot.learning")

SLANG_PROMPT = (
    "你是群聊黑话整理助手。从下面的群聊记录中提取 0~3 个「黑话/梗/缩写/圈内用语」。\n"
    "要求：\n"
    "1. 只提取有明确上下文能推断含义的；不确定就不要输出；\n"
    "2. 不要提取普通词、人名、游戏名、命令、链接；\n"
    "3. term 是群里实际出现的写法（2~16 字），meaning 用一句中文解释（2~60 字）；\n"
    "4. confidence 是你对含义的把握，0~1；\n"
    "5. 只输出严格 JSON 数组，元素形如 "
    '{"term":"...","meaning":"...","confidence":0.8}；没有就输出 []。\n'
    "不要输出 JSON 以外的任何字。"
)

IMPRESSION_PROMPT = (
    "你是群聊观察助手。根据下面这个人最近的发言，写 1~2 条简短印象"
    "（说话风格 / 常聊话题 / 态度），每条不超过 30 字。\n"
    "只写有依据的；不要编造隐私、不要评价外貌或现实身份；"
    "信息不足就输出 []。\n"
    "只输出严格 JSON 数组，例如 [\"说话直，常聊终末地\",\"喜欢熬夜\"]。"
)


def _parse_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    # 去掉 ```json ... ``` 围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                continue
    try:
        return json.loads(text)
    except ValueError:
        return None


def _transcript(rows: list[dict], limit_chars: int = 5000) -> str:
    lines: list[str] = []
    for r in rows[-80:]:
        who = (r.get("display_name") or "").strip() or (
            f"用户{(r.get('openid') or '')[-6:]}"
        )
        text = " ".join((r.get("content") or "").split())[:200]
        if text:
            lines.append(f"{who}: {text}")
    out = "\n".join(lines)
    return out[-limit_chars:]


async def learn_slang(
    gctx: group_context.GroupContextStore,
    mem: memory_store.MemoryStore,
    group_openid: str,
    limit: int = 40,
    max_terms: int = 3,
) -> list[dict]:
    rows = await gctx.recent(group_openid, limit=limit)
    if len(rows) < 4:
        return []
    raw = await persona.raw_complete(SLANG_PROMPT, _transcript(rows))
    data = _parse_json(raw or "")
    if not isinstance(data, list):
        log.info("[学习] 黑话：模型没有返回 JSON，跳过")
        return []
    scope = f"group:{group_openid}"
    added: list[dict] = []
    for item in data[:max_terms]:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()[:16]
        meaning = str(item.get("meaning") or "").strip()[:120]
        if len(term) < 2 or len(meaning) < 2:
            continue
        if sensitive.filter.hit(term) or sensitive.filter.hit(meaning):
            continue
        try:
            conf = float(item.get("confidence") or 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        if conf < 0.5:
            continue
        if await mem.add_slang(scope, term, meaning, "auto", conf):
            added.append({"term": term, "meaning": meaning, "confidence": conf})
    if added:
        log.info("[学习] 黑话 %d 条：%s", len(added), "、".join(x["term"] for x in added))
    return added


async def learn_impressions(
    gctx: group_context.GroupContextStore,
    mem: memory_store.MemoryStore,
    group_openid: str,
    limit: int = 80,
    max_users: int = 3,
    min_messages: int = 3,
) -> int:
    rows = await gctx.recent(group_openid, limit=limit)
    if not rows:
        return 0
    by_user: dict[str, dict] = {}
    for r in rows:
        oid = r.get("openid") or ""
        if not oid:
            continue
        item = by_user.setdefault(
            oid, {"name": (r.get("display_name") or "")[:24], "texts": []}
        )
        text = " ".join((r.get("content") or "").split())[:200]
        if text:
            item["texts"].append(text)
    candidates = [
        (oid, item)
        for oid, item in by_user.items()
        if len(item["texts"]) >= min_messages
    ]
    candidates.sort(key=lambda x: len(x[1]["texts"]), reverse=True)
    scope = f"group:{group_openid}"
    learned = 0
    for oid, item in candidates[:max_users]:
        transcript = "\n".join(item["texts"][-20:])
        raw = await persona.raw_complete(IMPRESSION_PROMPT, transcript)
        data = _parse_json(raw or "")
        if not isinstance(data, list):
            continue
        notes = [
            str(x).strip()[:40]
            for x in data
            if isinstance(x, str) and len(str(x).strip()) >= 2
        ][:2]
        if not notes:
            continue
        await mem.set_impression(scope, oid, "；".join(notes), "auto")
        learned += 1
    if learned:
        log.info("[学习] 人物印象 %d 人（群 %s）", learned, group_openid[:10])
    return learned


async def learn_group(
    gctx: group_context.GroupContextStore,
    mem: memory_store.MemoryStore,
    group_openid: str,
) -> tuple[int, int]:
    """学一个群：返回 (黑话条数, 印象人数)。"""
    slang = await learn_slang(gctx, mem, group_openid)
    imps = await learn_impressions(gctx, mem, group_openid)
    await mem.set_last_learn(f"group:{group_openid}", time.time())
    return len(slang), imps


async def learn_all(
    gctx: group_context.GroupContextStore,
    mem: memory_store.MemoryStore,
    lookback_minutes: float = 240.0,
    max_groups: int = 5,
    min_new_messages: int = 6,
) -> tuple[int, int]:
    """自动学习一轮：最近活跃的群，diff 足够才学。"""
    if not memory_store.enabled():
        return 0, 0
    groups = await gctx.active_groups(lookback_minutes)
    total_slang = 0
    total_imps = 0
    for gid in groups[:max_groups]:
        scope = f"group:{gid}"
        last = await mem.get_last_learn(scope)
        rows = await gctx.recent(gid, limit=80, since_ts=last)
        if len(rows) < min_new_messages:
            continue
        try:
            s, i = await learn_group(gctx, mem, gid)
            total_slang += s
            total_imps += i
        except Exception as exc:  # noqa: BLE001
            log.warning("[学习] 群 %s 失败：%s", gid[:10], exc)
    return total_slang, total_imps


async def poll_loop(
    gctx: group_context.GroupContextStore,
    mem: memory_store.MemoryStore,
    minutes: float,
    max_groups: int = 5,
) -> None:
    """后台自动学习：先跑一轮，再按间隔循环。"""
    import asyncio

    interval = max(60.0, float(minutes) * 60.0)
    while True:
        try:
            s, i = await learn_all(gctx, mem, max_groups=max_groups)
            if s or i:
                log.info("[学习] 本轮：黑话 %d 条，人物印象 %d 人", s, i)
        except Exception as exc:  # noqa: BLE001
            log.warning("[学习] 本轮失败：%s", exc)
        await asyncio.sleep(interval)
