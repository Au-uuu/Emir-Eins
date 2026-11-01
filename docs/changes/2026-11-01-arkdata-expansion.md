# 明日方舟数据扩展：面板 / 天赋 / 模组 / 敌人 / 材料 / 每日更新

- 日期：2026-11-01
- 模块：`arkdata.py`、`arklore.py`、`arkenemy.py`、`penguin.py`、`arkdata_update.py`、`tools/build_arkdata.py`
- 数据：`data/arkdata.json.gz`、`data/arklore.json.gz`、`data/arkenemy.json.gz`、`data/material_drops.json.gz`、`data/arkdata.meta.json`

## 需求

1. 干员面板要能按精英阶段/等级查，默认「精二 90、满信赖、0 潜能」；
2. 补天赋、模组；
3. 补敌人档案；
4. 材料掉率可以查企鹅物流（penguin-stats.cn）；
5. 不做关卡/活动查询；
6. 每天 12:00 检查上游数据是否有更新。

## 数据源与口径

- PRTS 网页从腾讯云服务器访问会 403，不能直接抓；改用 `closure-studio/ArknightsGamedataPure` 的离线表快照。
- 面板按精英阶段的等级关键帧**线性插值**；信赖加成按 `favorKeyFrames` 缩放；默认潜能 0、无模组。
- 模组属性取该模组最高等级的 `attributeBlackboard`；效果说明取 `overrideTraitDataBundle` / `addOrOverrideTalentDataBundle` 的 `additionalDescription`，并代入 `blackboard` 数值。
- 敌人档案来自 `enemy_handbook_table.json`；材料掉率来自企鹅物流公开 API，生成的本地缓存只保留每件材料效率最高的几个关卡。
- 所有数值只回答快照里有的；查不到返回空，交给联网或让模型直说没查到，**不凭记忆编数字**。

## 改动

### 1. 干员数值 `arkdata.py`

- 新增意图：天赋、模组/模块。
- 面板支持 `精二 90`、`精一 50`、`90 级`、`无信赖` 等写法；默认精二 90 满信赖。
- 输出示例：
  - `塔露拉天赋` → 两个天赋的名称与描述；
  - `银灰模组` → 模组名/类型/属性加成/最高级效果。
- `data/arkdata.json.gz` 重新生成，包含 1251 个干员、等级关键帧、信赖、天赋、模组。

### 2. 语音 / 档案 `arklore.py`

- `data/arklore.json.gz` 收录 444 个干员的语音与档案，按需取一小段，不整本塞进 BM25 知识库。

### 3. 敌人档案 `arkenemy.py`

- `data/arkenemy.json.gz` 收录 1776 种敌人。
- 按敌人名匹配「敌人/怪物/首领/boss/机制」等意图，输出等级、伤害类型、档案描述。

### 4. 材料掉率 `penguin.py`

- 首次使用生成 `data/material_drops.json.gz`（120 种材料，约 6 KB），之后查询秒回。
- 按关卡代号 `code` 去重，避免同一关卡因不同 `stageId` 重复出现；保留样本量大的那条。
- 输出按「理智/个」升序，最多 5 个关卡，标注样本量。
- 实测：`固源岩在哪刷` → 1-7，124.5%，约 4.8 理智/个；`聚酸酯块掉落` → 14-9 只出现一次。

### 5. 每日更新检查 `arkdata_update.py`

- 每天 12:00 检查 7 张上游表的 commit：
  `character_table`、`skill_table`、`charword_table`、`handbook_info_table`、`uniequip_table`、`battle_equip_table`、`enemy_handbook_table`。
- 只提醒管理员群，不自动下载 20MB+ 的表。
- 状态文件 `data/arkdata_update.json`；快照来源 commit 在 `data/arkdata.meta.json`。

### 6. 快照构建工具 `tools/build_arkdata.py`

- 一条命令重建 `arkdata.json.gz` + `arkenemy.json.gz`：
  ```bash
  python tools/build_arkdata.py                 # 自动下载最新表
  python tools/build_arkdata.py --src <目录>    # 用已下载的 excel/*.json
  ```
- 产物只保留查询需要的字段，不打包整张原始表。

## 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_BOT_ARKDATA` | `data/arkdata.json.gz` | 干员快照路径 |
| `QQ_BOT_ARKLORE` | `data/arklore.json.gz` | 语音/档案快照路径 |
| `QQ_BOT_ARKENEMY` | `data/arkenemy.json.gz` | 敌人档案路径 |
| `QQ_BOT_ARKDATA_CHECK` | `1` | 每日更新检查开关 |
| `QQ_BOT_ARKDATA_CHECK_HOUR` | `12` | 检查小时 |
| `QQ_BOT_ARKDATA_CHECK_GROUP` | 管理员群 | 提醒发到哪个群 |

## 测试

- `_test_arkdata.py`：面板插值、等级、信赖、天赋、模组、未收录返回空。
- `_test_arklore.py`：语音/档案命中与不命中。
- `_test_arkenemy.py`：敌人档案、等级/伤害类型、闲聊不注入。
- `_test_arkdata_update.py`：有更新提醒、同日不重复、无更新不提醒。
- `_test_penguin.py`：排序、掉率/理智计算、按 code 去重、缓存落盘、闲聊不注入。

## 已知限制

- PRTS 网页 403 是常态，必须走快照；快照更新靠每日提醒 + 手动重建。
- 企鹅物流首次建缓存可能较慢（服务器实测约 4~5 分钟），之后走本地缓存。
- 模组效果只有 353/511 个能取到说明，其余只报属性，不编效果。
- 敌人只做「这是什么敌人」，不做关卡攻略。
