# -*- coding: utf-8 -*-
"""每天 12:00 检查明日方舟数值/语音/档案快照是否有上游更新。

只检查、不自动下载 20MB+ 的表：发现上游相关表的 commit 变了，就给管理员群
发一条提醒，让我们更新 `arkdata.json.gz` / `arklore.json.gz`。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from datetime import datetime

log = logging.getLogger("qqbot.arkdata_update")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_PATH = os.path.join(DATA_DIR, "arkdata_update.json")
META_PATH = os.path.join(DATA_DIR, "arkdata.meta.json")

REPO = "closure-studio/ArknightsGamedataPure"
BRANCH = "main"
PATHS = (
    "excel/character_table.json",
    "excel/skill_table.json",
    "excel/charword_table.json",
    "excel/handbook_info_table.json",
)
CHECK_SECONDS = 300.0


def enabled() -> bool:
    return (os.getenv("QQ_BOT_ARKDATA_CHECK", "1") or "1").strip().lower() not in (
        "0", "false", "off", "no", ""
    )


def check_hour() -> int:
    try:
        hour = int(os.getenv("QQ_BOT_ARKDATA_CHECK_HOUR", "12") or 12)
    except ValueError:
        hour = 12
    return max(0, min(23, hour))


def _group_id() -> str:
    return (
        os.getenv("QQ_BOT_ARKDATA_CHECK_GROUP", "")
        or os.getenv("QQ_BOT_ADMIN_GROUP", "")
        or ""
    ).strip()


def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_json(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("[数值更新] 写状态失败：%s", exc)


def _http_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "qqbot-arkdata-check/1.0",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _latest_commit(path: str) -> dict:
    url = (
        f"https://api.github.com/repos/{REPO}/commits"
        f"?path={path}&sha={BRANCH}&per_page=1"
    )
    rows = _http_json(url)
    if not isinstance(rows, list) or not rows:
        return {}
    row = rows[0]
    commit = row.get("commit") or {}
    return {
        "path": path,
        "sha": (row.get("sha") or "")[:12],
        "date": (commit.get("committer") or {}).get("date", ""),
        "message": ((commit.get("message") or "").splitlines() or [""])[0][:120],
    }


def _snapshot_commits(meta: dict) -> dict[str, str]:
    stored = meta.get("source_commits")
    if isinstance(stored, dict) and stored:
        return {str(k): str(v)[:12] for k, v in stored.items()}
    fallback = str(meta.get("source_commit") or "")[:12]
    return {path: fallback for path in PATHS}


async def check_once(api, now: datetime | None = None, commits: dict | None = None) -> bool:
    """到 12 点后检查一次；返回是否真的发了更新提醒。"""
    if not enabled():
        return False
    now = now or datetime.now()
    if now.hour < check_hour():
        return False
    today = now.strftime("%Y-%m-%d")
    state = _load_json(STATE_PATH)
    if state.get("last_checked") == today:
        return False

    gid = _group_id()
    if not gid:
        log.info("[数值更新] 未配置管理员群，跳过")
        return False

    if commits is None:
        commits = {}
        for path in PATHS:
            try:
                commits[path] = await asyncio.to_thread(_latest_commit, path)
            except Exception as exc:  # noqa: BLE001
                log.warning("[数值更新] 查询 %s 失败：%s", path, exc)
                commits[path] = {}

    meta = _load_json(META_PATH)
    old = _snapshot_commits(meta)
    changed = [
        c for path, c in commits.items()
        if c.get("sha") and c.get("sha") != (old.get(path) or "")
    ]
    state["last_checked"] = today
    if not changed:
        log.info("[数值更新] 无更新")
        _save_json(STATE_PATH, state)
        return False

    changed.sort(key=lambda c: c.get("date") or "", reverse=True)
    latest = changed[0]
    new_sha = latest.get("sha") or ""
    state["last_seen"] = new_sha
    _save_json(STATE_PATH, state)

    if state.get("last_alerted") == new_sha:
        log.info("[数值更新] 已提醒过 %s，等快照更新", new_sha)
        return False

    detail = "\n".join(
        f"{c.get('path', '?').split('/')[-1]}: "
        f"{(old.get(c.get('path')) or '?')} -> {c.get('sha', '?')}"
        for c in changed
    )
    text = (
        "【数值/资料快照】检测到上游数据更新\n"
        f"{detail}\n"
        f"时间：{latest.get('date') or '未知'}\n"
        f"说明：{latest.get('message') or '—'}\n"
        "当前 arkdata.json.gz / arklore.json.gz 可能缺新内容，请更新快照。"
    )
    try:
        await api.post_group_message(group_openid=gid, content=text)
        state["last_alerted"] = new_sha
        _save_json(STATE_PATH, state)
        log.info("[数值更新] 已提醒管理员群：%s", new_sha)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("[数值更新] 提醒发送失败：%s", exc)
        return False


async def poll_loop(api) -> None:
    if not enabled():
        log.info("[数值更新] 未启用")
        return
    log.info("[数值更新] 已启动：每天 %02d:00 检查上游数据", check_hour())
    while True:
        try:
            await check_once(api)
        except Exception as exc:  # noqa: BLE001
            log.warning("[数值更新] 本轮异常：%s", exc)
        await asyncio.sleep(CHECK_SECONDS)
