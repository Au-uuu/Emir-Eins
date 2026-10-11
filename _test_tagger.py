"""离线打标（多模型注册表：Camie 默认 / AnimeTimm 可选）：模型缺失时的降级、
结果缓存、每标签 best_threshold、标签表解析、异常兜底。

不依赖 onnxruntime / 模型文件：推理层（`tag_image_sync`）整个替换成假的，
只验证可离线测试的逻辑。本机没有模型文件时也能跑。
"""
import asyncio
import hashlib
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tagger  # noqa: E402

failures = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + extra) if extra else ''}")
    if not ok:
        failures += 1


async def main() -> int:
    # ---------------- 1. 模型缺失 ----------------
    print("\n[1] 模型文件缺失：静默禁用，不抛异常")
    os.environ["QQ_BOT_TAGGER_DIR"] = os.path.join(
        tempfile.gettempdir(), "no_such_tagger_model"
    )
    tagger._failed = False
    tagger._session = None
    tagger._cache.clear()

    check("available() 为假", tagger.available() is False)
    result = await tagger.tag_image(b"whatever")
    check("返回空对", result == ([], []), str(result))
    check("模型没加载时不写缓存", len(tagger._cache) == 0, str(len(tagger._cache)))

    # ---------------- 2. 缓存：同一张图只推理一次 ----------------
    print("\n[2] 缓存：同一张图只推理一次")
    calls: list = []

    def fake_sync(data):  # 注意是**同步**函数：它在线程池里跑
        calls.append(data)
        return ["hatsune_miku"], ["vocaloid"], []

    tagger.tag_image_detail_sync = fake_sync
    tagger._session = ("fake-session", [], [], 448)  # 让 tag_image 认为模型已加载
    tagger._cache.clear()

    r1 = await tagger.tag_image(b"same-bytes")
    r2 = await tagger.tag_image(b"same-bytes")
    check("两次结果一致", r1 == r2 == (["hatsune_miku"], ["vocaloid"]), str(r2))
    check("同一张图只推理了一次", len(calls) == 1, str(len(calls)))
    check("缓存里有一条", len(tagger._cache) == 1, str(len(tagger._cache)))

    await tagger.tag_image(b"other-bytes")
    check("不同内容要重新推理", len(calls) == 2, str(len(calls)))
    check("缓存里两条", len(tagger._cache) == 2, str(len(tagger._cache)))

    # ---------------- 3. 缓存上限（LRU） ----------------
    print("\n[3] 缓存上限：超了淘汰最旧的")
    tagger._cache.clear()
    calls.clear()
    total = tagger._CACHE_MAX + 5
    for i in range(total):
        await tagger.tag_image(f"img-{i}".encode())

    check(
        f"条目数不超过 {tagger._CACHE_MAX}",
        len(tagger._cache) == tagger._CACHE_MAX,
        str(len(tagger._cache)),
    )
    key_first = hashlib.sha256(b"img-0").hexdigest()
    key_last = hashlib.sha256(f"img-{total - 1}".encode()).hexdigest()
    check("最旧的已被淘汰", key_first not in tagger._cache)
    check("最新的还在", key_last in tagger._cache)

    # ---------------- 4. 推理抛异常 ----------------
    print("\n[4] 推理抛异常：返回空对、不写缓存")
    tagger._cache.clear()

    def boom(data):
        raise RuntimeError("模拟推理失败")

    tagger.tag_image_detail_sync = boom
    result = await tagger.tag_image(b"boom-bytes")
    check("返回空对", result == ([], []), str(result))
    check("异常时不写缓存", len(tagger._cache) == 0, str(len(tagger._cache)))

    # ---------------- 5. 日志格式 ----------------
    print("\n[5] 打标结果的日志格式（可观测性）")
    check(
        "有标签时列出作品与角色",
        tagger._fmt_tags((["hatsune_miku"], ["vocaloid"])) == "作品=vocaloid；角色=hatsune_miku",
        tagger._fmt_tags((["hatsune_miku"], ["vocaloid"])),
    )
    check(
        "无标签时明确说没命中",
        "无标签命中" in tagger._fmt_tags(([], [])),
        tagger._fmt_tags(([], [])),
    )

    # ---------------- 6. 注册表与每标签 best_threshold ----------------
    print("\n[6] 注册表 + 每标签 best_threshold")
    check(
        "注册表含 camie 与 animetimm-swinv2",
        "camie" in tagger.MODELS and "animetimm-swinv2" in tagger.MODELS,
        str(list(tagger.MODELS)),
    )
    check("默认模型是 camie", tagger.DEFAULT_MODEL == "camie")

    # _select：实际阈值 = max(每标签 best_threshold, 类别默认阈值)
    probs = [0.9, 0.5, 0.4, 0.7]
    entries = [(0, "a", None), (1, "b", 0.8), (2, "c", None), (3, "d", 0.2)]
    got = tagger._select(probs, entries, base_thr=0.5)
    # a:0.9>=0.5 中；b:0.5>=max(0.8,0.5)=0.8 不中；c:0.4>=0.5 不中；d:0.7>=max(0.2,0.5)=0.5 中
    check("best_threshold 生效（b 被 0.8 拦下、d 用 0.5 命中）", got == ["a", "d"], str(got))
    check("按置信度降序", got and got[0] == "a", str(got))
    check(
        f"最多 {tagger.MAX_TAGS} 个",
        len(tagger._select([0.9] * 10, [(i, str(i), None) for i in range(10)], 0.5))
        == tagger.MAX_TAGS,
    )

    # _candidates：低于硬阈值也给出低置信候选，供模型核对
    cands = tagger._candidates(
        [0.42, 0.31, 0.20, 0.55],
        [(0, "a", None), (1, "b", None), (2, "c", None), (3, "d", None)],
    )
    check(
        "候选：排序 + 过滤 <0.25 + 最多 3 个",
        cands == [("d", 0.55), ("a", 0.42), ("b", 0.31)],
        str(cands),
    )

    # ---------------- 7. 标签表解析（不依赖模型文件） ----------------
    print("\n[7] 标签表解析")
    camie_meta = {
        "dataset_info": {
            "tag_mapping": {
                "idx_to_tag": {"0": "hatsune_miku", "1": "1girl", "2": "vocaloid"},
                "tag_to_category": {
                    "hatsune_miku": "character",
                    "1girl": "general",
                    "vocaloid": "copyright",
                },
            }
        }
    }
    tags, cat = tagger._parse_camie_tags(camie_meta)
    check("camie idx_to_tag 解析", tags == ["hatsune_miku", "1girl", "vocaloid"], str(tags))
    check("camie 类别映射", cat.get("vocaloid") == "copyright", str(cat))

    import csv as _csv

    fd, path = tempfile.mkstemp(suffix=".csv", text=True)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["tag_id", "name", "category", "count", "best_threshold"])
        w.writerow(["1", "1girl", "0", "100", "0.30"])
        w.writerow(["2", "hatsune_miku", "4", "50", "0.75"])
        w.writerow(["3", "miku_(vocaloid)", "4", "50", ""])  # 空 best
    rows = tagger._parse_animetimm_rows(path)
    os.remove(path)
    check("animetimm CSV 行序 = 下标", len(rows) == 3 and rows[1][1] == "hatsune_miku", str(rows))
    check("category 保留原值", rows[1][0] == "4", str(rows))
    check("best_threshold 解析", rows[1][2] == 0.75, str(rows))
    check("空 best_threshold → None", rows[2][2] is None, str(rows))

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
