# 修正 Noto CJK 取到日文字形 + 新增文本转图片模块

日期：2026-09-28

## 需求

1. `/help` 等长文本在 QQ 里占满整屏，想把文本转成图片来显示得小一点。
2. 排查渲染方案时发现字体选择有 bug。

## 改动

### 1. 修 `_load_cjk_font()` 取到日文字形（bug）

`NotoSansCJK-Regular.ttc` 里打包了 JP/KR/SC/TC/HK 五套字形：

| index | 字形 |
|---|---|
| 0 | **Noto Sans CJK JP** ← 原代码用的（默认值） |
| 1 | KR |
| 2 | **SC（简体中文）** |
| 3 | TC |
| 4 | HK |

原来的 `ImageFont.truetype(path, size)` 不带 `index`，取的是 **index=0 = 日文字形**，
简体中文会出现「直」「骨」等字的日式写法。

新增 `_ttc_face_index()`，对 Noto CJK 的 ttc 明确取 index=2；序号不被支持时回退默认值。
这个修复**同时改善了游戏名片拼图**（`make_card_sheet` 用的也是这个字体加载函数）。

### 2. 服务器装 Noto Sans CJK

服务器原先只有「文泉驿正黑」（`wqy-zenhei`），观感差一档。
`apt-get install fonts-noto-cjk` 后，`_FONT_CANDIDATES` 里第一项即可命中。

### 3. 新增 `text_image.py`（尚未接入）

`render_text_image(text, width=1280, font_size=28, ...) -> bytes`，返回 PNG bytes，
可直接交给现有 `uploader` 发送。能力：

- 按**像素宽度**折行：中文逐字断，英文优先在空格处断
- 行距 / 段距 / 内边距 / 配色可调
- 2 倍超采样后缩小，边缘更干净
- `max_height` 保护，避免生成畸形长图

**「显示更小」的原理**：QQ 把图片按聊天窗口宽度等比缩放，所以

```
屏上字号 ≈ font_size × (聊天窗口宽度 / 画布宽度)
```

画布越宽，缩放越多，字越小。实测三档（手机聊天窗口按 400pt 估）：

| 画布 | 手机上约 |
|---|---|
| 1080 | 10.4pt |
| 1280 | 8.7pt |
| 1600 | 7.0pt |

参考：QQ 默认文本约 16~17pt。

## 改动文件

| 文件 | 说明 |
|---|---|
| `image_store.py` | 新增 `_ttc_face_index()`，`_load_cjk_font()` 据此选字形 |
| `text_image.py` | 新增，文本转图片模块 |

## 测试方式

- 渲染 `bot.HELP_TEXT` / `bot.ADMIN_HELP_TEXT` 共 4 张样例，检查折行、行距、无豆腐块。
- 确认实际命中字体为 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`。
- 样例图在 `C:\DSH\preview\`（未入库）。

## 备注（接入前必须决策）

⚠️ **QQ 的按钮（keyboard）只能挂在文本消息上**，`/help` 现在带三个按钮
（功能与指令 / 随机来张图 / 图库列表），**改成图片后按钮会丢失**。接入前需要三选一：

1. 保持文本 + 按钮（现状）
2. 改成纯图片，放弃按钮
3. 折中：短文本 + 按钮，长内容另配一张图（占两次被动回复额度）

另外：图片消息同样消耗被动回复额度，体积 350~550KB，要走过分片上传，比回文本稍慢。

**接入还未进行**，`text_image.py` 目前只是部署在服务器上（`/opt/qqbot/text_image.py`），
`bot.py` 没有任何地方调用它。
