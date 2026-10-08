"""WD14 打标：模型缺失时的降级、结果缓存、异常兜底。

不依赖 onnxruntime / 模型文件：推理层（`tag_image_sync`）整个替换成假的，
只验证 `tag_image` 的缓存与容错逻辑。本机没有模型文件时也能跑。
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
        return ["hatsune_miku"], ["vocaloid"]

    tagger.tag_image_sync = fake_sync
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

    tagger.tag_image_sync = boom
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

    print(f"\n{'=' * 50}")
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
