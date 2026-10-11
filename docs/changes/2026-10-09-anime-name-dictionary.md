# 角色译名接可靠词库：修「curren_chan→当前酱」+ 卡片信任线索

日期：2026-10-09

## 现象

用户发现 `curren_chan` 被模型翻成「当前酱」（字面直译 current+chan），而非正确的中文名。

## 根因

打标器输出的是 **danbooru 标签（罗马音/英文）**，之前让**视觉模型临时翻译**——遇到
拼音/双关/多词标签就按字面硬翻（curren_chan→当前酱、still_in_love→仍然相爱）。

## 方案：接入社区维护的中英对照词库

- 词库：[`ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table`](https://github.com/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table)
  （MIT，每日更新，33 万+ 标签；字段 `name/category/cn_name/post_count`，
  category 3=作品、4=角色）。实测：`curren_chan_(umamusume)→真机伶（赛马娘）`、
  `ceobe_(arknights)→刻俄柏`、`still_in_love_(umamusume)→爱如往昔`，均正确。
- 新增 `names.py`：按标签查 `cn_name`（只读、按主键查、缺库静默降级）。
- `bot.py::tagger_hints()`：把查到的中文名直接拼进线索，
  如 `角色=curren_chan_(umamusume)（真机伶（赛马娘））`，模型照抄即可，不再自己翻。

## 角色卡同步

1. 原「把标签翻译成中文名」→ 改为「括号内是词库中文名，直接采用；**没有中文名时不要硬翻**」。
2. 新增「**优先信任线索、接受同人差异**」：打标线索给的角色优先信任；同人/二创常与原设不同，
   **不要拿「官方设定不符」去否认线索**（此前因「Ceobe 官方是银灰短发」而反驳正确的
   `ceobe_(arknights)` 标签，属误判）。
3. 译名示例改用不会误导的例子（不再出现佩丽卡）。

## 改动文件

| 文件 | 说明 |
|---|---|
| `names.py` | 新增，查标签中文名 |
| `bot.py` | `tagger_hints` 注入中文名 |
| `persona/依蜜尔爱因.md` | §8 译名规则 + 信任线索 + 接受同人差异 |
| `_test_names.py` | 新增：查表/缺库降级/空值 |
| `.env.example` | `QQ_BOT_TAG_ZH_DB` 说明 |

## 部署

词库不进代码包，需单独放到服务器：

```bash
# 本地经代理下载后 scp，或服务器能直连时 curl
curl -L -o tag.sqlite https://raw.githubusercontent.com/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table/main/tag.sqlite
scp tag.sqlite ubuntu@<server>:/tmp/tag_zh.sqlite
ssh ubuntu@<server> "sudo mv /tmp/tag_zh.sqlite /opt/qqbot/data/tag_zh.sqlite && sudo chmod 644 /opt/qqbot/data/tag_zh.sqlite"
```

- 默认路径 `data/tag_zh.sqlite`（`data/` 已被 `.gitignore` 忽略）；可用 `QQ_BOT_TAG_ZH_DB` 覆盖。
- **更新**：词库每日更新，想刷新就重下再覆盖、重启即可。

## 测试

- `_test_names.py`：**0 失败**（查表命中、空白/NULL → None、缺库降级、未收录）。
- 服务器实跑 `tagger_hints` 确认中文名注入（龙骧 / 卡比 / 镜音铃 / 芙宁娜…）。
- 本地 venv 无该词库时静默降级，其它套件不受影响。

## 备注

- `cn_name` 形如「角色名（作品）」，拼进线索后会有嵌套括号
  （`tag（真机伶（赛马娘））`），信息冗余但清晰；如嫌啰嗦可只取括号前的主名。
- 词库为 LLM 翻译 + 人工校对，绝大多数可靠；个别冷门标签仍可能不准，卡片已声明「与画面矛盾时以画面为准」。
