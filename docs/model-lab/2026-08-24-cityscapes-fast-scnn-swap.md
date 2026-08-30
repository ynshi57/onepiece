# 户外通行分割：Fast-SCNN 权重从「室内地板」换到「Cityscapes 街景」（Phase 1）

日期：2026-08-24　主责：全麦（模型）　裁决：乔布斯（可走类别集合）　配合：罗根（Core ML/端上契约）

## 背景（根因）

诊断台的 region IoU 显示 iPhone「感知的可走绿区」明显不准：会把**天空/建筑当可走**，
`region_false_go_frames=118`、precision 仅 0.80。

根因不是架构 bug，是**域不匹配**：端上模型是 `Tanishjain9/fast-scnn-floor-segmentation`
——一个为**室内地板**训练的 Fast-SCNN，被拿去跑户外 CamVid 街景。Fast-SCNN 架构本身
就是为 Cityscapes（户外驾驶）设计并 benchmark 的，所以**换权重即可**，不必换架构、不必改端上 Swift。

## 改动（现成权重，可 Core ML 转换）

- **权重来源**：[Tramac/Fast-SCNN-pytorch](https://github.com/Tramac/Fast-SCNN-pytorch)
  `weights/fast_scnn_citys.pth`（GitHub 直下，~4.7MB，Cityscapes 19 类）。不需要 HuggingFace。
- **转换脚本**：`deploy/ios/convert_fast_scnn_cityscapes_pth_to_coreml.py`
  （架构照 Tramac **原样**嵌入 → `strict=True` 加载，权重不匹配会**大声报错**而非静默半载）。
  两个关键点（旧 floor 脚本都缺）：
  1. **19 类 → 2 通道契约**：加固定「归约头」，把 19 类 logits 按语义分成
     {可走 = road(0) + sidewalk(1)} 与 {其余 17 类}，各做数值稳定的 `logsumexp` →
     输出 `[1, 2, H, W]`，**通道 0 = notTrav、通道 1 = trav**。
     数学上 `sigmoid(trav − notTrav) = P(road∪sidewalk)`，正好对上端上
     `LocalSegmentation.sampler(fromMultiArray:)`，**Swift 一行不改**。
  2. **ImageNet 归一化**：Cityscapes 权重训练时做了 `(x−mean)/std`。Core ML `ImageType`
     把 0-255 图缩到 [0,1]（scale=1/255），模型 `forward` 内部再做 ImageNet 归一化，
     喂给网络它训练时见过的分布。旧 floor 脚本漏了这步，直接用会静默崩精度。
- **一键脚本**：`deploy/ios/convert_fast_scnn_cityscapes_to_coreml.sh`
  （下载 → 转换 → `coremlcompiler` 编译 → 安装进 App）；`setup_mac.sh` models profile 已切到它。
- **可走类别集合**（唯一安全相关旋钮，乔布斯裁决）：road + sidewalk。
  想调只改 `TRAVERSABLE_CLASS_IDS` 一个常量；加 `terrain(9)` 提 recall 但会把草地/土坡算进可走。

## 结果（CamVid 701 帧，region 指标）

| 指标 | Floor（旧·室内地板） | Cityscapes（新·户外） | Δ |
|---|---|---|---|
| mean_iou | 0.625 | **0.774** | +0.149 |
| mean_recall | 0.645 | **0.793** | +0.148 |
| mean_precision | 0.804 | **0.973** | +0.169 |
| **region_false_go_frames**（安全关键：幻觉可走） | 118 | **0** | **−118** |
| region_miss_frames | 158 | **83** | −75 |

引导线同步变好：`hit_rate 0.851`、`false_go_frames 0`、`missed_path_frames 10`、`both_ok 691/701`。

安全语义：**precision 0.97 + false-go 归零**意味着"模型说可走的地方基本真的可走"，
不再把天空/建筑幻觉成路——这正是 VQASee「辅助而非接管、不误导」的底线。
剩余的 `region_miss_frames=83`（recall 0.79）是"偏保守、漏报部分可走"，方向安全。

## 验证

- 转换后 **Core ML vs PyTorch 数值一致**：随机图 traversable-prob 最大差 0.0034（fp16/插值级）。
- 输出形状 `[1,2,512,512]`，通道序经 predict 校验对上 Swift 契约。
- harness 701 帧：`predicted=701 missing_image=0 decode_errors=0`。
- 新基线已存：`camvid-ios{,-region,-guidance}`（作为后续回归门禁的新地板）。
- `pytest server-vqa/tests/test_region_grid.py` 13 passed；`bash -n` 两个新脚本通过。

## 影响面（变更影响面规则）

- 触及**共享资源**：端上模型文件 `VQASeeTraversabilitySegmentation.mlmodelc`（App + 符号链接的 harness 同一份，运行时加载，已生效）。
- 触及**安装面**：`setup_mac.sh` models profile 已切换；旧 `convert_floor_segmentation_onnx_to_coreml.sh` 保留作回退。
- 触及**基线/门禁**：`eval-baselines/camvid-ios*` 已更新为新模型数值。
- 端上 Swift 契约（2 通道 logits）**未变**，无需改 App/harness 代码。
- 旧 floor 模型已备份到 `~/.cache/vqasee/models/VQASeeTraversabilitySegmentation.floor.mlmodelc.bak`，可一键回退。

## 下一步

- **Phase 2（长期正解）**：在 CamVid（+BDD，+后续 iPhone 真机帧）上**微调** Fast-SCNN，
  对着我们自己的 region eval 集优化，把 recall（当前 0.79）继续拉高、把 `region_miss_frames` 压下去。
- **Phase 0（可选止血）**：天空/上部抑制 + `segTraversablePixel` 阈值扫——现在 false-go 已 0，优先级下降。
- 端上真机延迟/内存回归待在 iOS profile 上验证（本轮为离线 harness，未在真机 App 上计时）。
