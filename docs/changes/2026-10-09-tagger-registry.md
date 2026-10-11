# 离线打标注册表化：Camie 默认 + AnimeTimm 可选，补每标签 best_threshold

日期：2026-10-09

## 背景

上一轮把 WD14 换成 Camie（见 [camie-tagger](2026-10-09-camie-tagger.md)），但 `tagger.py`
只支持单一模型。用户希望抄社区包 [nestflow/ComfyUI-Booru-Tagger](https://github.com/nestflow/ComfyUI-Booru-Tagger)
的做法：注册表驱动、可换模型，并把「每标签 best_threshold」补上。

## 服务器规格与选型（SSH 实测）

| 项 | 值 |
|---|---|
| CPU | 4 核 Intel Xeon Platinum 8255C @ 2.50GHz |
| 内存 | 总 3.6G（已用 1.9G，可用 ~1.8G）+ swap 1.9G |
| 磁盘 | 40G（可用 25G） |
| GPU | 无 → 纯 CPU |

CPU 推理耗时≈输入像素数×参数量。据此：

- ✅ **Camie v2（默认）**：143M / 512²，角色库 2.6 万+、含作品标签、免登录。
- ✅ **AnimeTimm swinv2_base（可选）**：99.7M / **256²**（像素仅 Camie 的 1/4），
  Danbooru v4、带 best_threshold；但只有角色、GPL-3.0 且需 HF 登录。
- ❌ PixAI v1.0（486M / 1008²，像素 16×）、WD/AnimeTimm 的 eva02-large、convnextv2-huge
  （300~700M）——纯 CPU + 3.6G 内存上不实用。

**同一时刻只加载一个模型**（内存紧张），选择在启动读环境变量时定死，不做运行时热切换。

## 借鉴 nestflow 的点

- **注册表**：每模型登记 `dir / model / metadata / preprocess / family / 阈值`，
  对照它的 `models.json`。
- **每标签 best_threshold**：实际阈值取 `max(每标签 best_threshold, 类别默认阈值)`，
  与它的 `np.maximum(best, threshold)` 一致。
- **AnimeTimm 预处理**：照抄它的 `PadToSize → Resize → CenterCrop → Normalize`
  （`_animetimm_fit` / `_animetimm_resize_size` 的几何语义），并用 PIL 实现（我们的进程
  不引入 torch）。

## 设计

`tagger.py` 重写为注册表驱动：

- `MODELS` 字典登记 `camie` 与 `animetimm-swinv2`；`QQ_BOT_TAGGER_MODEL` 选择（默认 camie），
  `QQ_BOT_TAGGER_DIR` 覆盖目录，阈值仍可用 `QQ_BOT_TAGGER_CHAR_THRESHOLD` /
  `..._COPYRIGHT_THRESHOLD` / `QQ_BOT_TAGGER_THRESHOLD` 覆盖。
- **标签表解析分家族**：Camie 读 JSON（`tag_to_category`），AnimeTimm 读 `selected_tags.csv`
  （`category` 数字：0=general/4=character/9=rating，附 `best_threshold`）。
- **预处理分家族**：Camie 直缩 + ImageNet + NCHW；AnimeTimm 读模型自带的
  `preprocess.json`（缺失时用注册表里的兜底参数），走 PadToSize→Resize→CenterCrop→Normalize。
- 推理层统一：sigmoid → `_select(probs, entries, base_thr)`（纯 Python，按
  `max(best, base)` 过滤、按置信度排序、最多 `MAX_TAGS` 个）。
- 对外 API 不变（`available()` / `tag_image(bytes)` → `(角色, 作品)`），`bot.py` 无需改逻辑。

## 改动文件

| 文件 | 说明 |
|---|---|
| `tagger.py` | 注册表驱动重写（Camie 默认 + AnimeTimm 可选、best_threshold、双家族预处理） |
| `_test_tagger.py` | 新增 [6] 注册表/best_threshold、[7] 标签表解析断言 |
| `.env.example` | `QQ_BOT_TAGGER_MODEL` 与两个模型的说明 |
| `bot.py` | `tagger_hints()` 文档措辞改为模型无关 |
| `persona/依蜜尔爱因.md` | §8 去掉「（Camie）」的硬编码 |
| `docs/CHANGELOG.md` | 索引 |

## 部署

Camie（默认）沿用上一轮：

```bash
mkdir -p /opt/qqbot/models/camie-tagger-v2 && cd /opt/qqbot/models/camie-tagger-v2
curl -L -o camie-tagger-v2.onnx        https://hf-mirror.com/Camais03/camie-tagger-v2/resolve/main/camie-tagger-v2.onnx
curl -L -o camie-tagger-v2-metadata.json https://hf-mirror.com/Camais03/camie-tagger-v2/resolve/main/camie-tagger-v2-metadata.json
```

可选 AnimeTimm（换模型时用；gated，需先登录 HF 同意条款）：

```bash
mkdir -p /opt/qqbot/models/animetimm-swinv2_base-dbv4-full
cd /opt/qqbot/models/animetimm-swinv2_base-dbv4-full
# 用带 token 的 huggingface-cli 下载 model.onnx / selected_tags.csv / preprocess.json
# 然后在 .env 设 QQ_BOT_TAGGER_MODEL=animetimm-swinv2 并重启
```

## 实际部署记录（2026-10-09）

- `deploy/upload.ps1`：上传 31 个 `.py` + `persona/`，逐个 SHA256 全部 `[OK]`；
  服务器 17 个测试套件全 **0 失败**；服务 `active`，机器人已上线。
- **踩坑**：上一轮只把「下载 Camie」写进文档，**没真下**，服务器上只有旧的 WD14 →
  新代码找不到 `models/camie-tagger-v2/`，打标一度禁用（`available: False`）。
- **服务器下不动模型**：服务器能连 `hf-mirror.com` 根域名（200），但下载具体文件报
  **`curl rc=35`（SSL 连接失败）**，`huggingface.co` 直接超时；ModelScope 无该模型镜像。
- **处理**：在本地经 Clash 代理下载 ONNX（**752 MB**，hf-mirror 直连只返回 4090 字节的
  跳转页，必须走代理），再 `scp` 上传（实测 2.73 MB/s，273 秒）到
  `/opt/qqbot/models/camie-tagger-v2/`。
- **端到端验证**：`available: True`，模型加载成功；打库内一张图 → `hatsune_miku`；
  首张 6.5 秒（含加载）、第二张 2.5 秒；加载后可用内存仍有 2.6 G。
- **遗留**：旧 WD14 模型（467 MB）仍在 `models/wd-swinv2-tagger-v3/`，新代码已不用，可删。

> 教训：以后换模型要**当场把权重部署到位**，别只写文档；且服务器没有可用代理，
> HF 系权重基本只能「本地经代理下载 → scp 上传」。

## 测试

- `_test_tagger.py`：**0 失败**（含新增 [6] 注册表 + best_threshold、[7] 标签表解析）。
- `_test_persona.py`：**0 失败**。
- 合成数据：Camie JSON（`idx_to_tag`/`tag_to_idx`）、AnimeTimm CSV（含空 best_threshold）、
  阈值 `max(best, base)` 语义，均通过。
- ⚠️ 本机 venv 无 `onnxruntime`/`numpy`，真实推理与 `preprocess.json` 解析只能在服务器验证；
  AnimeTimm 分支因模型 gated，本地无法端到端实测。

## 备注

- AnimeTimm 分支的 `preprocess.json` 解析是防御式的：文件在就用其参数，缺失/异常则用注册表
  兜底（swinv2_base 按 256²）。上线后看日志确认实际用的是哪套。
- 未实现运行时热切换模型：注册表是给「部署时选一个」用的，切换需改 `.env` + 重启。
