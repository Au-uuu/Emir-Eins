# 事实类回答开启 low 思考强度

- 日期：2026-11-02
- 模块：`persona.py`、`bot.py`
- 配置：`QQ_BOT_CHAT_MAX_TOKENS=4096`、`QQ_BOT_TEXT_REASONING_EFFORT`（可选）

## 现象

群友反馈“聊天回话有点降智”。查日志：

- 所有聊天调用都是 `reasoning=0`；
- 线上 `QQ_BOT_TEXT_THINKING=disabled`；
- 实际聊天 `in=6000~9000`、`out=37~99`，延迟 2.4~4.2 秒。

也就是说：回复速度是优先了，但模型完全没有思考过程，事实类问题容易“凭印象下结论”。

## A/B 实测

用同一个人设、同一个问题：

> 塔露拉三技能总伤 176335，持续 36 秒，敌人 30% 减伤，实际 DPS 多少？值不值得专三？

| 模式 | 延迟 | 输出/推理 token | 结果 |
|---|---|---|---|
| 关闭思考 | 3.1s | 36 / 0 | 数字算对，但直接说“专三值”，没有依据 |
| `low` | 8.8s | 558 / 509 | 数字算对，明确说“没查到技能数据，不敢乱判”，更谨慎 |
| `high` | 60.1s | 348 / 292 | 数字算对，但说“塔露拉没实装”，事实错误 |

闲聊场景：

| 模式 | 延迟 | 输出 |
|---|---|---|
| 关闭思考 | 8.8s（波动） | 31 字 |
| `low` | 5.3s | 243 字，内容更展开 |

结论：

- **不建议全局开 `high/max`**：实测 60 秒，而且更容易过度推理、答错事实。
- `low` 对事实类问题有“先核对再下结论”的效果，但会多 2~5 秒。
- 普通闲聊不需要思考，保持关闭。

## 改动

- `persona.py`
  - 新增 `text_reasoning_effort()` / `vl_reasoning_effort()`，读 `QQ_BOT_TEXT_REASONING_EFFORT` / `QQ_BOT_VL_REASONING_EFFORT`。
  - `generate(..., reasoning=None)` 支持单次覆盖思考强度。
  - `_request_sync(..., reasoning=None)`：显式传 `reasoning` 时临时打开思考，并带 `reasoning_effort`（low/high/max）。
- `bot.py`
  - 只有 `factual=True`（命中数值/资料/知识库/联网/梗库）的回复传 `reasoning="low"`；
  - 普通闲聊仍不传，保持 `QQ_BOT_TEXT_THINKING=disabled` 的速度。
- 线上 `.env`：`QQ_BOT_CHAT_MAX_TOKENS` 从 `2048` 提到 `4096`，给推理 token 留余量。
- 测试 fake `generate` 统一接受 `**kwargs`；`_test_persona.py` 新增断言：传 `reasoning="low"` 时 payload 带 `thinking.enabled` + `reasoning_effort=low`。

## 测试

- `_test_persona.py`：payload 思考强度、静默策略、消息拆分等全部通过。
- `_test_video_frames.py`、`_test_chat_style.py`、`_test_group_context.py`、`_test_profiles.py` 回归通过。
- `_test_meme.py`、`_test_bili.py`、`_test_arkdata.py`、`_test_penguin.py`、`_test_usage_report.py` 通过。

## 备注

- `high` 的 60 秒是单次实测，不代表每次都会这么慢；但风险足够大，不作为默认。
- 如果以后觉得事实类还是慢，可以把 `reasoning="low"` 改成只在联网/复杂数值题启用；普通数值查询继续关闭。
- 思考强度不能替代资料注入：游戏数值仍应优先查 `arkdata` / `arklore` / KB，思考只是在没有现成答案时减少“硬编”。
