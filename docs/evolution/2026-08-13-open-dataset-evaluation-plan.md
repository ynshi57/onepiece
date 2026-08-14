# Open Dataset Evaluation Plan

Date: 2026-08-13

## 乔布斯裁决

真机验证太慢、样本太少，必须接入开源测试数据形成离线闭环。目标不是替代真机，而是让 VQASee 能在大量室内/室外/道路图像和视频上自动发现问题。

## What is implemented now

- `server-vqa/app/path_dataset_eval.py`
  - Common evaluator for path guidance manifests.
  - Computes status accuracy, focus direction accuracy, risk misses, false blocks, unknown rate, and recommendations.
- `server-vqa/tools/evaluate_path_guidance_dataset.py`
  - CLI entry point.
- `docs/datasets/path-guidance-manifest-example.jsonl`
  - Tiny example manifest.
- `server-vqa/tests/test_path_dataset_eval.py`
  - Unit tests for risk-miss and prediction override behavior.

## Command

```bash
source .venv/bin/activate
python server-vqa/tools/evaluate_path_guidance_dataset.py docs/datasets/path-guidance-manifest-example.jsonl
```

## Next implementation tasks

### T1: Dataset adapters

Build adapters that convert each downloaded dataset into the common manifest:

- ScanNet adapter: indoor RGB-D/video/semantic annotations → indoor path labels.
- ADE20K adapter: floor/road/wall/object labels → static image path labels.
- BDD100K adapter: drivable area / lane / objects → road-risk path labels.
- Mapillary adapter: road/sidewalk/curb/object labels → outdoor path labels.

### T2: Prediction generation

Run VQASee local path guidance on images/video frames and write:

```json
{"frame_id":"...", "path_guidance": {...}}
```

### T3: Continuous video stability

For datasets with video, compute:

- path status flicker rate;
- focus direction jitter;
- stale/in-flight equivalent metrics when running through backend.

### T4: CI sample pack

Commit a very small license-safe synthetic/sample pack, not full datasets, to make tests deterministic.

## Role assignments

- 乔布斯: define release thresholds and which dataset failures block release.
- 罗根: dataset adapters, CLI, video stability metrics.
- 全麦: model prediction generation and failure taxonomy.
- 思余: visual report format for overlay/flicker review.

## Success criteria

- At least 100 indoor frames evaluated offline.
- At least 100 outdoor/road frames evaluated offline.
- Report identifies risk misses and false blocks automatically.
- Diagnostics map back to task suggestions.
- Raw data remains outside Git.

## 2026-08-13 执行记录：闭环平台数据集评估 UI

已完成：

- `start_diagnostics_platform.sh`
  - 一键启动 VQASee 闭环实验平台，不启动 Qwen。
- `server-vqa/app/path_manifest_export.py`
  - 将真机诊断 session + labels + path_guidance prediction 导出为统一 path-guidance JSONL manifest。
- 新增平台入口：
  - `/diagnostics/sessions/{session_id}/path-manifest`
  - `/diagnostics/sessions/{session_id}/path-eval`
  - `/diagnostics/sessions/{session_id}/path-eval/ui`
  - `/diagnostics/datasets/ui`
  - `/diagnostics/datasets/evaluate?manifest=...`
  - `/diagnostics/datasets/evaluate/ui?manifest=...`
- `server-vqa/tests/test_path_manifest_export.py`
  - 覆盖诊断 session 到 dataset manifest 的转换。
- `server-vqa/tests/test_api.py`
  - 覆盖 session path manifest / path eval / dataset eval UI。

使用方式：

```bash
bash start_diagnostics_platform.sh
```

打开：

```text
http://127.0.0.1:9000/diagnostics/ui
```

平台现在有：

- 真机 Sessions；
- 标注；
- 引导层可视化；
- 评估报告；
- 导出路径 manifest；
- 路径评估；
- 开源/本地数据集评估。

验证：

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

结果：99 passed。

## 2026-08-14 执行记录：数据集导入工具与平台评估

已新增工具：

### 1. 图片 + mask → path manifest

```bash
source .venv/bin/activate
python server-vqa/tools/create_path_manifest_from_masks.py \
  --images /path/to/images \
  --masks /path/to/traversability_masks \
  --output docs/datasets/my-indoor-manifest.jsonl \
  --split indoor \
  --tag office \
  --tag floor
```

约定：

- mask 高/白色 = traversable/floor/drivable；
- mask 低/黑色 = non-traversable/obstacle/unknown；
- 工具会计算 near/left/right ROI 覆盖率，生成 ground_truth。

注意：iOS/Vision ROI 使用左下原点，图像 mask 使用左上原点，adapter 已做坐标转换。

### 2. video → frames + manifest

```bash
source .venv/bin/activate
python server-vqa/tools/extract_video_frames.py /path/to/video.mp4 \
  --output-dir /path/to/frames \
  --manifest docs/datasets/my-video-manifest.jsonl \
  --every 30 \
  --split indoor-video \
  --tag office
```

该 manifest 默认没有 ground_truth，可用于人工标注或后续模型预测。

### 3. 诊断 session → path manifest

平台新增：

```text
/diagnostics/sessions/{session_id}/path-manifest
/diagnostics/sessions/{session_id}/path-eval/ui
```

### 4. 数据集评估 UI

平台新增：

```text
/diagnostics/datasets/ui
/diagnostics/datasets/evaluate/ui?manifest=docs/datasets/path-guidance-manifest-example.jsonl
```

把开源数据或本地视频转出的 manifest 放到 `docs/datasets/`，平台会自动列出。

验证：

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

当前结果：99+ tests expected after full run。

## 2026-08-14 执行记录：平台内创建/浏览数据集 manifest

已完成平台 UI 增强：

- `/diagnostics/datasets/create/ui`
  - 网页表单：输入 images 目录、masks 目录、输出 manifest、split、tags、threshold、limit。
  - 生成 VQASee path-guidance manifest。
- `/diagnostics/datasets/manifest/ui?manifest=...`
  - 浏览 manifest 中的每一帧。
  - 显示原图、mask、ground truth、prediction。
- `/diagnostics/local-file?path=...`
  - 仅允许预览仓库目录、`/tmp`、`/private/tmp` 或 `VQASEE_DATASET_ROOT` 下的本地文件。
- `/diagnostics/datasets/evaluate/ui?manifest=...`
  - 评估 manifest 并展示报告。

闭环平台现在可以支持：

```text
本地图片 + mask 目录
→ 平台网页创建 manifest
→ manifest 浏览/预览图片和 mask
→ 运行路径评估
→ 输出 risk_miss / false_block / unknown 等指标
```

使用建议：

```bash
bash start_diagnostics_platform.sh
```

打开：

```text
http://127.0.0.1:9000/diagnostics/ui
```

点击：

```text
开源数据集评估 → 从图片+mask目录创建 manifest
```

验证：

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

结果：101 passed。

## 2026-08-14 追加：Dataset Hub 向导式导入

根据用户反馈，平台不能把 `images/masks/output/split/tags/threshold/limit` 等工程参数作为主流程。已更新规则和 UI：

- AGENTS.md 新增“工具也要产品化”：内部平台也必须使用向导式流程、默认值、渐进披露。
- Product/UI skills 新增内部工具产品化原则。
- `/diagnostics/datasets/create/ui` 改为向导式：
  1. 选择数据类型：室内 / 室外 / 道路；
  2. 选择图片目录；
  3. 选择 mask 目录；
  4. 高级设置折叠：output、split、tags、threshold、limit。
- 不填 output 时自动生成：`docs/datasets/auto-{dataset_type}-manifest.jsonl`。
- 不填 split/tags 时按数据类型生成默认值。

验证：

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

结果：102 passed。
