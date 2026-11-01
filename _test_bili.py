# -*- coding: utf-8 -*-
"""B 站链接解析测试（不联网）。"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bili  # noqa: E402

failures = 0


def check(label, ok, extra=""):
    global failures
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra and not ok else ''}")
    if not ok:
        failures += 1


def test_pick_url():
    print("[1] 链接识别")
    cases = [
        ("看看这个 https://www.bilibili.com/video/BV1GJ411x7h7 好笑", "BV1GJ411x7h7"),
        ("https://b23.tv/AbCd123", "b23.tv/AbCd123"),
        ("https://m.bilibili.com/video/av80433022", "av80433022"),
    ]
    for text, want in cases:
        got = bili.pick_url(text)
        check(f"识别 {want}", want in got, got)
    check("普通链接不误判", bili.pick_url("https://example.com/video/BV1GJ411x7h7") == "")
    check("没有链接返回空", bili.pick_url("今天天气不错") == "")


def test_format_meta():
    print("[2] 元数据")
    view = {
        "title": "【官方 MV】Never Gonna Give You Up - Rick Astley",
        "owner": {"name": "Rick Astley"},
        "duration": 213,
        "desc": "  一段  简介\n第二行  ",
    }
    text = bili._format_meta(view, 9, False)
    check("标题", "Never Gonna Give You Up" in text, text)
    check("UP 主", "UP 主：Rick Astley" in text, text)
    check("时长", "3:33" in text, text)
    check("简介压成一行", "一段 简介 第二行" in text, text)
    check("帧数", "9 帧" in text, text)
    check("无帧不带帧说明", "帧" not in bili._format_meta(view, 0, False))


def test_play_url():
    print("[3] 播放流选择")
    fixture = {
        "code": 0,
        "data": {
            "dash": {
                "video": [
                    {"baseUrl": "hevc-480", "codecs": "hev1", "height": 480},
                    {"baseUrl": "avc-480", "codecs": "avc1", "height": 480},
                    {"baseUrl": "avc-360", "codecs": "avc1", "height": 360},
                    {"baseUrl": "", "codecs": "avc1", "height": 240},
                ]
            }
        },
    }
    orig = bili._get_json
    bili._get_json = lambda url, retry=True: fixture
    try:
        got = bili._play_url({"bvid": "BV1GJ411x7h7", "cid": 1})
    finally:
        bili._get_json = orig
    check("优先 avc 且低清", got == "avc-360", got)


def test_parse_metadata_only():
    print("[4] 只取元数据")
    view = {"bvid": "BV1GJ411x7h7", "title": "测试视频", "duration": 60, "owner": {"name": "UP"}}
    orig_view, orig_resolve = bili._view, bili._resolve
    bili._view = lambda bvid="", aid="": view
    bili._resolve = lambda url: "https://www.bilibili.com/video/BV1GJ411x7h7"
    try:
        text, frames = bili.parse("https://b23.tv/xyz", want_frames=False)
    finally:
        bili._view, bili._resolve = orig_view, orig_resolve
    check("返回标题", "测试视频" in text, text)
    check("不抽帧", frames == [], frames)

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    test_pick_url()
    test_format_meta()
    test_play_url()
    test_parse_metadata_only()
    sys.exit(1 if failures else 0)
