"""在服务器上构建知识库：萌娘百科抓取 + 便宜 Qwen 模型整理，写成 data/kb/<作品>.md。

- 角色名单来自 data/tag_zh.sqlite（Danbooru 中英对照表，category=4）。
- 正文来自萌娘百科（MediaWiki API）；**消歧义页自动改用搜索定位**到该作品的角色页。
- 先用正则粗筛（去游戏数值段 + 丢空段），再交给便宜的 Qwen 模型润色成「设定条目」。
- 只调用便宜模型，额度/限流失败时自动轮换下一个模型。
- 带磁盘缓存，重跑不重复烧 token。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, "/opt/qqbot")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/opt/qqbot/.env")

MOEGIRL = "https://zh.moegirl.org.cn/api.php"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) qqbot-kb/1.0"}
QWEN_KEY = (os.getenv("QQ_BOT_QWEN_KEY") or "").strip()
QWEN_URL = os.getenv("QQ_BOT_QWEN_URL") or (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)
# 模型轮换表：优先便宜且额度足的，前面用得多，后面兜底。
# 2026-10-10 按百炼后台实际额度重排（旧的 qwen3.8-flash / qwen3.7-flash / qwen3.7-plus 已用尽）。
# 跨厂商（deepseek/kimi/glm）同样走 DashScope 兼容端点，实测均可调通。
LLM_MODELS = [
    "qwen3.6-flash", "qwen3.5-flash", "qwen3.6-flash-2026-04-16", "qwen3.5-flash-2026-02-23",
    "qwen3.6-27b", "qwen3.6-35b-a3b", "qwen3.6-plus", "qwen3.5-plus", "qwen3.6-plus-2026-04-02",
    "deepseek-v4-pro", "kimi-k2.6", "kimi-k2.5", "kimi-k2.7-code",
    "glm-5", "glm-5.1", "glm-5.2",
    "qwen3.7-max", "qwen3.7-max-2026-06-08", "qwen3.7-plus-2026-05-26", "deepseek-v4-pro-0813",
    "qwen3.8-max",  # 只剩 ~18%，垫底用
]
TAG_DB = "/opt/qqbot/data/tag_zh.sqlite"
OUT_DIR = "/opt/qqbot/data/kb"
CACHE_DIR = "/opt/qqbot/data/kb_cache"

# (输出文件, Danbooru 后缀, 萌百作品页, 搜索用作品名, 角色名 inc/exc)
WORKS = [
    ("明日方舟.md", "arknights", "明日方舟", "明日方舟", None, "终末地"),
    ("终末地.md", "arknights", "明日方舟：终末地", "终末地", "终末地", None),
    ("原神.md", "genshin_impact", "原神", "原神", None, None),
    ("赛马娘.md", "umamusume", "赛马娘 Pretty Derby", "赛马娘", None, None),
    ("崩坏星穹铁道.md", "honkai:_star_rail", "崩坏：星穹铁道", "星穹铁道", None, None),
    ("彩虹六号围攻.md", "rainbow_six_siege", "彩虹六号：围攻", "彩虹六号", None, None),
    # —— 第二批扩面（2026-10-09）——
    ("蔚蓝档案.md", "blue_archive", "蔚蓝档案", "蔚蓝档案", None, None),
    ("碧蓝航线.md", "azur_lane", "碧蓝航线", "碧蓝航线", None, None),
    ("胜利女神妮姬.md", "nikke", "胜利女神：妮姬", "妮姬", None, None),
    ("绝区零.md", "zenless_zone_zero", "绝区零", "绝区零", None, None),
    ("鸣潮.md", "wuthering_waves", "鸣潮", "鸣潮", None, None),
    ("重返未来1999.md", "reverse:1999", "重返未来：1999", "重返未来", None, None),
    ("崩坏3.md", "honkai_impact", "崩坏3", "崩坏3", None, None),
    # —— 第三批：Danbooru 无统一作品后缀，改走「手写名单」（2026-10-10）——
    # 魔裁（魔法少女的魔女审判）：词表里是散标签（sakuraba_ema…），没有统一后缀
    ("魔法少女的魔女审判.md", None, "魔法少女的魔女审判", "魔法少女的魔女审判", None, None,
     ["樱羽艾玛", "二阶堂希罗", "夏目安安", "城崎诺亚", "莲见蕾雅", "佐伯米莉亚", "宝生玛格",
      "黑部奈叶香", "紫藤亚里沙", "橘雪莉", "远野汉娜", "泽渡可可", "冰上梅露露", "月代雪",
      "典狱长", "看守"]),
    # 蓝色大肥鱼 = DeepSeek 的鲸鱼娘拟人；萌百页面就是「DeepSeek娘」，单角色、无作品页
    ("蓝色大肥鱼.md", None, None, "蓝色大肥鱼", None, None, ["蓝色大肥鱼"]),
]

HEADING_RE = re.compile(r"^(={2,})\s*(.+?)\s*\1\s*$")
KEEP_HEADING = re.compile(
    r"简介|介绍|角色简介|角色介绍|角色经历|经历|角色相关|人物相关|人物关系|"
    r"人物简介|设定|背景|世界观|性格|外貌|形象|能力|故事|剧情|档案|履历|密录"
)
MAX_RAW = 900

# 估算 token 预算护栏：这几个模型开了「用完即停」，超额度只会失败、不会计费，
# 故设一个大上限仅作异常保护（如死循环）；正常靠模型轮换 + 用完即停兜底。
BUDGET = 4_000_000
_TOK = {"in": 0, "out": 0}
_STOP = False

# 免费额度观测：每个模型记一个状态，只在「状态变化」时打印，避免刷屏。
# quota = 额度用尽（硬停）；httpXXX = 其它错误（多半是瞬时限流，遇下一个模型即可）。
QUOTA_HINT = re.compile(r"quota|exhaust|arrear|balance|insufficient|欠费|额度", re.I)
_MODEL_STATE: dict[str, str] = {}
_STATE_LOCK = threading.Lock()


def _note_model(model: str, state: str) -> None:
    global _STOP
    with _STATE_LOCK:
        prev = _MODEL_STATE.get(model)
        _MODEL_STATE[model] = state
        all_quota = all(_MODEL_STATE.get(m) == "quota" for m in LLM_MODELS)
    if state != prev and state != "ok":
        print(f"  [额度] {model} → {state}", flush=True)
    if all_quota and not _STOP:
        print("  ⚠ 轮换表里的模型额度都提示用尽，停止本轮（换额度/加模型后再跑；已缓存的不会重复）", flush=True)
        _STOP = True


def _est(text: str) -> int:
    return int(len(text) / 1.5) + 1

_llm_lock = threading.Lock()
_llm_idx = 0


def _get(params: dict) -> dict:
    url = MOEGIRL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_page(title: str) -> tuple[str, str, bool]:
    """返回 (实际标题, 正文, 是否消歧义页)。"""
    d = _get({"action": "query", "prop": "extracts|pageprops", "explaintext": "1",
              "ppprop": "disambiguation", "redirects": "1", "format": "json", "titles": title})
    pages = d.get("query", {}).get("pages", {})
    if not pages:
        return title, "", False
    p = next(iter(pages.values()))
    return p.get("title", title), (p.get("extract") or ""), ("disambiguation" in (p.get("pageprops") or {}))


def search_titles(term: str, limit: int = 5) -> list[str]:
    """[已失效] 萌百封禁了 list=search（返回 action-notallowed）。

    保留函数仅为兼容旧调用；新逻辑见 resolve() 的「限定标题 + links」兜底。
    """
    try:
        d = _get({"action": "query", "list": "search", "srsearch": term,
                  "srlimit": str(limit), "format": "json"})
    except Exception:  # noqa: BLE001
        return []
    return [r["title"] for r in d.get("query", {}).get("search", [])]


def page_links(title: str) -> list[str]:
    """取某页指向的条目标题（main namespace），用于从消歧义页定位角色页。"""
    try:
        d = _get({"action": "query", "prop": "links", "titles": title,
                  "pllimit": "200", "format": "json"})
    except Exception:  # noqa: BLE001
        return []
    for p in d.get("query", {}).get("pages", {}).values():
        return [l["title"] for l in (p.get("links") or [])]
    return []


def _series_rank(link: str, series: str) -> int:
    """链接与本作品的相关度：精确「作品:角色」最高，带后缀次之，仅包含最低，无关 -1。

    萌百的角色子页面形如「碧蓝航线:大凤」；消歧义页「大凤」的 links 里会同时列出
    「碧蓝航线:大凤 / 舰队Collection:大凤 / 战舰少女:大凤」，靠这个排序挑对本作。
    """
    for sep in (":", "："):
        if link.startswith(series + sep):
            rest = link[len(series) + 1:]
            plain = not any(ch in rest for ch in "([（·")
            return 2 if plain else 1
    return 0 if series in link else -1


def resolve(title: str, series: str) -> str:
    """拿到设定正文。

    直接命中（非消歧义）就返回；否则兜底定位到本作品的角色页：
      ① 试限定标题「作品:角色」「角色（作品）」「角色(作品)」；
      ② 读消歧义页的 links，按与本作品的相关度挑最合适的一条。
    （原先的 list=search 兜底已被萌百封禁，见 search_titles 注释。）
    """
    _, extract, disambig = fetch_page(title)
    if extract and not disambig:
        return extract
    for cand in (f"{series}:{title}", f"{title}（{series}）", f"{title}({series})"):
        _, ex2, dis2 = fetch_page(cand)
        if ex2 and not dis2:
            return ex2
    if extract:  # 消歧义页才有必要翻链接
        ranked = sorted((( _series_rank(l, series), l) for l in page_links(title)),
                        key=lambda x: -x[0])
        for rank, link in ranked:
            if rank < 0:
                break
            _, ex3, dis3 = fetch_page(link)
            if ex3 and not dis3:
                return ex3
    return ""


def rough_clean(extract: str) -> str:
    """按标题切段，保留设定类且**非空**的段，去 markdown 痕迹。"""
    sections = []
    cur_head, cur_body = None, []
    for line in extract.splitlines():
        m = HEADING_RE.match(line.strip())
        if m:
            sections.append((cur_head, cur_body))
            cur_head, cur_body = m.group(2), []
        else:
            cur_body.append(line)
    sections.append((cur_head, cur_body))

    parts = []
    for head, body in sections:
        body_text = "\n".join(body).strip()
        if head is None:
            if body_text:
                parts.append(body_text)
        elif KEEP_HEADING.search(head) and body_text:
            parts.append(head + "：" + body_text)
    text = "\n".join(parts)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"''+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 60:
        text = re.sub(r"\n{2,}", "\n", extract).strip()
    return text[:MAX_RAW].strip()


def llm_polish(text: str, kind: str) -> str:
    global _llm_idx, _STOP
    system = "你是资料整理助手，只输出整理后的正文。"
    user = (
        f"把下面这段《萌娘百科》原文整理成简洁的{kind}设定条目：\n"
        "- 只保留世界观/背景/身份/性格/外貌/关系/经历等「设定」事实；\n"
        "- 删掉游戏数值、技能、攻略、台词、面板、评价、注释、外部链接、广告、饭制调侃、"
        "消歧义列表、其它作品的同名条目；\n"
        "- 用简体中文，150~250 字，直接给正文，不要标题、不要「以下是」之类的套话；\n"
        "- 原文没有设定信息就只输出：无。\n\n原文：\n" + text
    )
    if _STOP or _TOK["in"] + _TOK["out"] >= BUDGET:
        _STOP = True
        return ""
    with _llm_lock:
        start = _llm_idx
    last = None
    for k in range(len(LLM_MODELS)):
        model = LLM_MODELS[(start + k) % len(LLM_MODELS)]
        body = json.dumps(
            {"model": model, "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": user}],
             "temperature": 0.3, "max_tokens": 300}
        ).encode()
        req = urllib.request.Request(
            QWEN_URL, data=body,
            headers={"Authorization": f"Bearer {QWEN_KEY}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            out = (d["choices"][0]["message"]["content"] or "").strip()
            _note_model(model, "ok")
            with _llm_lock:
                _llm_idx = (start + k) % len(LLM_MODELS)
            _TOK["in"] += _est(system) + _est(user)
            _TOK["out"] += _est(out)
            if len(out) <= 4 and out.rstrip("。.").strip() in ("无", ""):
                return ""
            return out
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                detail = ""
            state = "quota" if QUOTA_HINT.search(detail) else f"http{exc.code}"
            _note_model(model, state)
            last = f"HTTP {exc.code}: {detail[:120]}"
            continue
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            continue
    print(f"    LLM 全失败：{last}")
    return ""


def build_entry(title: str, kind: str, series: str) -> tuple[str, str]:
    if _STOP:
        return title, ""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp = os.path.join(CACHE_DIR, hashlib.sha256((series + "|" + title).encode()).hexdigest()[:16] + ".txt")
    if os.path.isfile(cp):
        with open(cp, encoding="utf-8") as fh:
            return title, fh.read()
    try:
        extract = resolve(title, series)
    except Exception as exc:  # noqa: BLE001
        print(f"    [x] {title} 萌百失败: {exc}")
        return title, ""
    if not extract:
        return title, ""
    raw = rough_clean(extract)
    if len(raw) < 40:
        return title, ""
    out = llm_polish(raw, kind)
    if not out:
        if len(raw) >= 160:  # LLM 失败/判定无设定时，长文退回粗筛、短文跳过
            out = raw[:500]
        else:
            return title, ""
    with open(cp, "w", encoding="utf-8") as fh:
        fh.write(out)
    return title, out


def enumerate_names(suffix: str, inc: str | None, exc: str | None) -> list[str]:
    conn = sqlite3.connect(TAG_DB)
    rows = conn.execute(
        "select cn_name from tags where category=4 and name like ?", (f"%({suffix})%",)
    ).fetchall()
    conn.close()
    out, seen = [], set()
    for (cn,) in rows:
        if not cn:
            continue
        base = re.sub(r"（[^（）]*）", "", cn).strip()
        if not base or base in seen:
            continue
        if inc and inc not in cn:
            continue
        if exc and exc in cn:
            continue
        seen.add(base)
        out.append(base)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--works", default=None,
                    help="只跑这些输出文件（逗号分隔），如 崩坏星穹铁道.md,彩虹六号围攻.md；默认全部")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    wanted = {x.strip() for x in args.works.split(",") if x.strip()} if args.works else None
    known = {w[0] for w in WORKS}
    if wanted:
        unknown = wanted - known
        if unknown:
            print(f"⚠ --works 里有未知文件：{', '.join(sorted(unknown))}（可选：{', '.join(w[0] for w in WORKS)}）")
        print(f"本次只跑：{', '.join(w for w in (x[0] for x in WORKS) if w in wanted)}", flush=True)

    for row in WORKS:
        filename, suffix, work_page, series, inc, exc = row[:6]
        manual = row[6] if len(row) > 6 else None
        if wanted and filename not in wanted:
            continue
        print(f"\n===== {filename}  （作品页《{work_page or '（跳过）'}》）=====", flush=True)
        names = list(manual) if manual else enumerate_names(suffix, inc, exc)
        if args.limit:
            names = names[: args.limit]
        print(f"  角色 {len(names)} 个", flush=True)
        blocks = []
        if work_page:  # 手写名单类（如 蓝色大肥鱼）可置空作品页，只出角色条目
            _, world = build_entry(work_page, "作品/世界观", series)
            if world:
                blocks.append(f"## {work_page}（作品/世界观）\n{world}")
        ok = 0
        with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = [pool.submit(build_entry, n, "角色", series) for n in names]
            for i, f in enumerate(cf.as_completed(futs)):
                title, text = f.result()
                if text:
                    blocks.append(f"## {title}\n{text}")
                    ok += 1
                if (i + 1) % 10 == 0:
                    print(f"  ...{i + 1}/{len(names)}（已收 {ok}，估算 {_TOK['in'] + _TOK['out']} tokens）", flush=True)
                if _STOP:
                    print(f"  ⚠ 触发 token 预算护栏（估算 {_TOK['in'] + _TOK['out']}），停止", flush=True)
                    break
        path = os.path.join(OUT_DIR, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n\n".join(blocks) + "\n")
        print(f"  写出 {filename}：角色 {ok}/{len(names)}，{os.path.getsize(path)} 字节", flush=True)

    print("\n===== 模型额度小结（本轮）=====", flush=True)
    for m in LLM_MODELS:
        print(f"  {m:18s} {_MODEL_STATE.get(m, '未调用')}", flush=True)
    print(f"累计估算 tokens：{_TOK['in'] + _TOK['out']}", flush=True)


if __name__ == "__main__":
    main()
