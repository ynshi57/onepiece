# CamVid 数据集根迁到仓库 dataset/

## 最终结论

`bayes` 不是下载代码写死的用户名，而是旧机器把绝对路径写进了 committed CamVid manifest。默认数据集根改为仓库 **`dataset/`**，读路径会 remap 旧绝对路径，新写入用仓库相对路径。CamVid 下载到 `dataset/camvid/`，git 忽略整个 `dataset/`。

真身感知 `predicted=0 missing_image=701` 的直接原因：诊断台把相对路径 `dataset/camvid/...` 交给**未重编**的旧 harness，旧逻辑把它接到 `docs/datasets/` 下面，变成不存在的 `docs/datasets/dataset/camvid/...`。图片其实在仓库根 `dataset/camvid/`。

## 方案要点

- 默认根：`<repo>/dataset`（`VQASEE_DATASET_ROOT` 仍可覆盖）
- 消费方 remap：`CamVid_RGB/` / `CamVid_Label/` 后缀 → 当前根
- 已提交 jsonl：`image_path` / `label_path` 改为 `dataset/camvid/...`
- `.gitignore`：`/dataset/`
- Swift harness：相对路径按仓库根解析，不再优先拼到 manifest 目录
- 诊断台：源码新于二进制时自动 `swift build`；启动前把 `image_path` 解析成真实文件

## 投票 / 共识

用户已明确要求修复并下载，按该方案执行。未改 `bayes-iphone` 客户端 ID。

## 已执行

- 新增 `server-vqa/app/dataset_paths.py`
- 诊断台默认根、文件服务、下载文案、zip 超时与镜像
- 适配器写入便携路径
- 三份 CamVid manifest 去 `/Users/bayes`
- 评测报告里的 baseline 绝对路径改为文件名
- Swift `resolveImagePath` 按仓库根解析；诊断台源码更新后重编 + 启动前解析路径
- 测试与 evolution 纪要
- CamVid 已落到 `dataset/camvid/`：RGB 701 + Label 701；`.gitignore` 的 `/dataset/` 已挡住二进制

## 待办 / 阻塞

- 全量 701 帧真身感知仍需用户在诊断台再点一次（本轮修路径 + 自动重编；未替用户跑完全量）

## 关键路径

```text
docs/datasets/camvid-manifest.jsonl
  → dataset/camvid/CamVid_RGB/*.png
  → /diagnostics/local-file 与 PerceptionHarness
```

## 风险与回滚

- 若本机仍把数据放在 `~/.cache/vqasee/open-datasets`，设 `VQASEE_DATASET_ROOT` 或把数据挪到 `dataset/`
- 相对路径依赖仓库根（`dataset/camvid` 或 `docs/datasets` 标记）；Python 启动前解析兜底
- 回滚：恢复默认 `~/.cache/vqasee/open-datasets` 并重新生成绝对路径 manifest（不推荐）

更新时间: 2026-09-10 13:55
