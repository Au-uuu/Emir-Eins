# 离线打标 WD14 → Camie Tagger v2：角色库更大更新

日期：2026-10-09

## 需求

用户反馈：现有离线打标（`SmilingWolf/wd-swinv2-tagger-v3`）的**角色库太老**——
训练数据停在 Danbooru 2024-02，终末地这类 2025+ 新作品的角色几乎不在标签表里。
要求调研并换用较新的开源离线打标模型。

## 调研结论（2026-10 现状）

| 模型 | 数据截止 | 角色标签数 | 角色 F1 | 输入 | 格式 | 许可证 |
|---|---|---|---|---|---|---|
| wd-swinv2-tagger-v3（旧） | 2024-02 | ~数千 | ~0.61 | 448 | ONNX+CSV | — |
| **Camie Tagger v2（采用）** | Danbooru 2024 | **26,968** | **0.834** | 512 | ONNX+JSON | GPL-3.0 |
| animetimm `dbv4-full` | Danbooru v4（2025 末） | 3,247 | 0.933 | 256~512 | ONNX+CSV | GPL-3.0（需登录） |
| PixAI Tagger v1.0 | 2026-05 | 8,308 | 0.924 | 1008 | safetensors+自定义码 | Apache-2.0 |
| danbooru-tag-query | danbooru2025 | — | 0.688(micro) | 448 | ONNX | DINOv3 License |
| WD Tagger v4 | — | — | — | — | — | 仅数据集，无权重 |

选择 **Camie Tagger v2**：角色标签量（2.6 万+）远超其他候选，直接对准「角色库太小」
的痛点；单文件 ONNX + metadata.json，改动可控。代价是 CPU 推理比 WD 慢、许可证为
GPL-3.0（仅自托管，不再分发权重）。

## 设计

- **`tagger.py`** 重写为 Camie v2：
  - 模型目录默认 `models/camie-tagger-v2/`，文件
    `camie-tagger-v2.onnx` + `camie-tagger-v2-metadata.json`；缺失/加载失败 → 静默禁用。
  - **预处理**（与官方 ONNX 示例一致）：RGBA 合成白底 → 直接缩放到输入边长
    （LANCZOS）→ /255 → ImageNet 归一化 → HWC→CHW → 加 batch 维（NCHW float32）。
    与旧版「白边补方 + RGB→BGR + NHWC」不同。
  - **标签解析**：读 `metadata['dataset_info']['tag_mapping']`，兼容 `idx_to_tag` /
    `tag_to_idx` 两种写法，按 `tag_to_category` 取 `character` / `copyright` 两类。
  - **阈值**：默认 0.5（官方 micro≈0.61、macro≈0.49 之间）；可用
    `QQ_BOT_TAGGER_CHAR_THRESHOLD` / `QQ_BOT_TAGGER_COPYRIGHT_THRESHOLD`，或统一的
    `QQ_BOT_TAGGER_THRESHOLD` 覆盖。
  - 输出仍是 logits，自行 sigmoid；缓存（sha256 + LRU 128）、线程池、失败静默、
    `_fmt_tags` 日志全部保留。
- **公开 API 不变**（`available()` / `tag_image(bytes)` → `(角色, 作品)`），
  `bot.py` 的 `tagger_hints()` 无需逻辑改动，只更新了措辞。
- **依赖**：`numpy` 显式加进 `requirements.txt`（新预处理直接用，之前靠
  onnxruntime 隐式带入）。

## 改动文件

| 文件 | 说明 |
|---|---|
| `tagger.py` | 重写为 Camie Tagger v2（预处理、metadata 解析、阈值） |
| `bot.py` | `tagger_hints()` 与看图处的注释/措辞更新（逻辑不变） |
| `persona/依蜜尔爱因.md` | §8 识别线索：WD14 → Camie |
| `requirements.txt` | 注释更新 + 显式 `numpy` |
| `.env.example` | 新增打标目录/阈值配置说明 |
| `_test_tagger.py` | 文档措辞更新（断言不变） |
| `_test_persona.py` | [7] 小节标题更新 |
| `.gitignore` | 排除 `models/`（打标模型体积大，不进仓库） |
| `docs/CHANGELOG.md` | 索引 |

## 部署

模型文件不进 git / 不进 data 备份，部署时在服务器下载：

```bash
mkdir -p /opt/qqbot/models/camie-tagger-v2
cd /opt/qqbot/models/camie-tagger-v2
curl -L -o camie-tagger-v2.onnx \
  https://hf-mirror.com/Camais03/camie-tagger-v2/resolve/main/camie-tagger-v2.onnx
curl -L -o camie-tagger-v2-metadata.json \
  https://hf-mirror.com/Camais03/camie-tagger-v2/resolve/main/camie-tagger-v2-metadata.json
```

旧的 `models/wd-swinv2-tagger-v3/` 可删。`pip install -r requirements.txt` 会补上
`numpy`（`onnxruntime` 早已安装）。

## 测试

- `_test_tagger.py`：**0 失败**（模型缺失降级、缓存、LRU、异常兜底、日志格式，
  均与旧实现同一契约，未改断言）。
- `_test_persona.py`：**0 失败**（含 [7] 打标线索进模型输入 / 无标签无线索前缀）。
- 合成数据校验：`idx_to_tag` / `tag_to_idx` 两种 metadata、类别大小写、阈值环境变量
  优先级与非法值回退，全部通过。
- ⚠️ 本机 venv 未装 `onnxruntime`/`numpy`，`_preprocess` 与真实推理只能在服务器验证。

## 待人工（部署后）

1. 日志出现 `Camie 打标已启用：…（输入 512px，角色标签 … 个，作品标签 … 个…）`
   = 首次打标成功。
2. 发 Danbooru 常见角色图 → 应报出角色名（中文译名由 qwen 完成）。
3. 发终末地立绘 → 对比 WD14：Camie 角色库更大，但仍受 Danbooru 收录限制。
4. 观察带图回复延迟与内存（模型 ~570MB、ONNX 会话常驻内存）。

## 词表覆盖实测（2026-10-09，查 Camie metadata）

以 Danbooru 命名检索三个游戏的角色标签（Camie 角色标签共 26,968，WD v3 为 2,751）：

| 游戏 | Camie 角色标签 | WD v3 | 说明 |
|---|---|---|---|
| 原神 `genshin_impact` | **217** | 70 | 大幅提升 |
| 明日方舟 `arknights`（不含 endfield） | **485** | 73 | 大幅提升 |
| 终末地 `endfield` | 1 | 0 | **仍然几乎没有** |

近两年新角色命中（Camie）：

- **原神（2024 起）**：命中 千织/阿蕾奇诺/克洛琳德/希格雯/艾梅莉埃/卡齐娜/玛拉妮/基尼奇/希诺宁/茜特菈莉/玛薇卡/丝柯克；
  **缺失** 恰斯卡/欧洛伦/蓝砚/梦见月瑞希/伊安珊/瓦蕾莎/爱可菲/伊法/伊涅芙/弗林斯/爱诺/菈乌玛/奈芙尔/达莉亚（多为 2025-5.4 之后）。
- **明日方舟（2024 起）**：命中 左乐/黍/乌尔比安/佩佩/维什戴尔/逻各斯/Mon3tr/拉普兰德/棘刺；缺失 娜仁图亚/文明之光/维娜/安提莱西亚等。
- **终末地**：Danbooru 上多挂 `_(arknights)` 而非 `_(endfield)`，命中的只有
  佩丽卡 `perlica_(arknights)`、管理员 `endministrator_(arknights)`、陈千语 `chen_qianyu_(arknights)`、
  安洁莉娜 `angelina_(endfield)_(arknights)`；乌尔加德/唐唐/阿德莉亚/吉尔伯塔/莱瓦汀/夏希/李锋等**全部缺失**。

> 结论：换 Camie 对**原神/明日方舟**是实打实的升级（3~6 倍覆盖 + 更多近两年角色），
> 但**终末地仍基本认不出**——Danbooru 对该游戏的标注太稀疏，这是静态打标器的天花板，
> 换更大的打标器也解决不了。终末地这类新游戏需另走图像特征检索或角色卡速查表。

## 已知限制

- Camie metadata/权重为 GPL-3.0：**只自托管、不再分发**。
- 只打标第一张图（多图消息的后续图不打标，省 CPU），沿用旧约定。
