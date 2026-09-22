# 2026-09-10 CamVid 数据集根迁到仓库 dataset/，去掉写死的 bayes 路径

## 现象

诊断台跑 CamVid 真身感知时 `predicted=0 missing_image=701`。stderr 里的图片路径是：

```text
/Users/bayes/.cache/vqasee/open-datasets/camvid/CamVid_RGB/*.png
```

这台机器上既没有 `/Users/bayes`，也没有 `~/.cache/vqasee/open-datasets/camvid`。

## 根因

不是下载脚本把用户名写死了。`create_camvid_manifest` 用 `str(path.resolve())` 把**当时那台机器的绝对路径**写进了 `docs/datasets/camvid-manifest*.jsonl`。旧 Mac 的 home 是 `bayes`，所以 701 行全部指向 `/Users/bayes/.cache/...`。Swift harness 对以 `/` 开头的路径当绝对路径，不会再映射。

默认下载根当时是 `~/.cache/vqasee/open-datasets`，换机后既对不上 committed manifest，也不在项目目录里。

## 修复

| 层 | 改动 |
|---|---|
| 默认根 | `_open_dataset_root()` → `<repo>/dataset`；仍可用 `VQASEE_DATASET_ROOT` 覆盖 |
| 读路径 | `dataset_paths.resolve_dataset_file`：缺文件时把 `CamVid_RGB/` / `CamVid_Label/` 后缀映射到当前根 |
| 写路径 | 仓库内文件写成 `dataset/camvid/...` 相对路径，不再 `resolve()` 进 home |
| harness | Swift 按仓库根解析相对路径；诊断台在源码新于二进制时自动重编，启动前把 `image_path` 解析成真实文件 |
| 白名单 | `_allowed_local_roots()` 继续派生自 `_open_dataset_root()`，与下载落盘同源 |
| git | `.gitignore` 增加 `/dataset/`，不提交 CamVid 二进制 |
| 已提交 manifest | 三份 jsonl 的 `image_path`/`label_path` 改为 `dataset/camvid/...` |
| 文案 | 诊断台提示改为仓库 `dataset/camvid` |

## 验证

- `pytest server-vqa/tests`（含 `test_dataset_paths.py`、stale-path 图片服务、默认根）
- 下载后确认 `dataset/camvid/CamVid_RGB` 有图，且 remap 能打开 committed jsonl 里的第一帧
- 真身感知 `predicted=0` 且路径落在 `docs/datasets/dataset/camvid/`：根因是 harness 二进制早于路径修复，且只在二进制缺失时才重编。已改为源码更新后自动重编，启动前把图片路径解析成真实文件
- drive 评估紫线走人行道：一键跑未注入 `role=vehicle` 和多类分割，二值模型把马路∪人行道都当可走，公交车一挡中心线就挤到人行道。已改为按 manifest `role` 覆盖配置并注入 Seg5

## 影响面

下载入口、Step 2 自动填充、`/diagnostics/local-file` 白名单、harness 读图、finetune 脚本读 jsonl。模型权重仍在 `~/.cache/vqasee/models`，这次不搬。

未改：`StreamingViewModel.defaultClientID = "bayes-iphone"`（设备 ID，不是数据集路径）。
