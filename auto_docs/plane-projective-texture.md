# 室内平面透视贴图（非 3DGS）

> 隐私说明：本文引用的真实场景图片、截图与派生对照图仅保存在本机，不随代码提交。

## 结论

用户要的实时线框+照片颜色已经接到扫描预览：观测平面用同 epoch 最正对的关键帧 JPEG 做透视贴图，对不上保持半透明色+线框。预览从 225 点门缝改为约 3:4（最高 460）。家具立牌本轮明确不做。3DGS 仍不上 App。

## 共识

- C 外观走投影贴图，不把 Brush/3DGS 塞进 iPhone。
- 数学与 Mac 侧 `spatial_reconstruction.camera` 对齐（列主序 K、`diag(1,-1,-1,1)`）。
- 真机贴图观感未验收；单测和离线看图不能代替。

## 已执行

- `SpatialProjectiveTexturing.swift`：选帧、投影、UV（SceneKit v 向上翻）。
- `SpatialScanScreen`：贴图材质、JPEG 晚到重试、预览加高、说明文案。
- `SpatialScanStore.scanDirectoryURL` / `controller.keyframeDirectory` 给 SceneKit 读 JPEG。
- `SpatialProjectiveTexturingTests` 三例模拟器通过。
- 看图门：`docs/evolution/figures/2026-10-12-projective-texture/look-gate.png`。

## 待办

- 真机看一张墙：方向对不对、斜视拉得能不能接受、直播半透明会不会挡路。
- 物体立牌（下一轮）。
- 曝光锁仍在工作区未单独验收安装。

## 风险

- SceneKit UIImage 可能再翻 V，真机才会暴露。
- 未融合的 ARKit 碎平面会各贴各的，接缝处会裂。
- 这不是完整房间纹理，也不是可通行承诺。

更新时间: 2026-10-12 18:30
