# 用量日报：去掉难读的 openid Top，只列高花费群名

- 日期：2026-11-12
- 模块：`usage_report.py`
- 配置：`QQ_BOT_USAGE_REPORT_TOP_CNY`（默认 `0.5`）

## 现象

日报最后一行是：

```text
Top：memory-learn $0.0120 / 群 5C04C69877 $0.0101 / 群 7AF61E9EA3 $0.0076 ...
```

- 全是 openid 前缀，看不懂；
- 金额只有几美分，参考价值低；
- `memory-learn`、`meme` 这类后台任务混在“群”里，更容易误解。

## 改动

- 删除原来的 `Top：...` 行。
- 改为只列**群聊**且当天花费超过 `QQ_BOT_USAGE_REPORT_TOP_CNY`（默认 **¥0.5**）的群：
  ```text
  高用量群（>¥0.5）：白、_樛木、Auuuu ¥0.83 / 另一个群 ¥0.61
  ```
- 群名通过 QQ 接口 `GET /v2/groups/{group_openid}/info` 获取，缓存到 `data/group_names.json`；
  查不到时回退为 `群 <openid前8位>`。
- 不列单聊、`memory-learn`、`meme` 等非群 scope；这些仍计入日报总额。
- 没有超过门槛的群时，整行不显示。

## 配置

```ini
QQ_BOT_USAGE_REPORT_TOP_CNY=0.5   # 0 = 显示所有有花费的群
```

## 测试

`_test_usage_report.py`：

- 默认 ¥0.5 时，低花费群不出现；
- 门槛调到 ¥0.01 时，群 A 出现、单聊 B 不出现；
- 假 API 返回 `group_name` 时，日报显示“测试群”；
- 原有发送、去重、22009 失败、补发逻辑不变。
