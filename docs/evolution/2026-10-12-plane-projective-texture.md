# 室内矢量贴图：平面透视贴图 + 加高预览

> 隐私说明：本文引用的真实场景图片、截图与派生对照图仅保存在本机，不随代码提交。

## 用户目标

实时线框加上照片颜色/纹理，不要 3DGS。家具立牌本轮不做。预览太近/太窄先把 225 点门缝裁切拿掉。

## 已证事实

- App 里没有 3DGS / splat / Brush 字符串。本轮也不引入。
- 关键帧 JPEG、列主序 K、`cameraToWorld`、`diag(1,-1,-1,1)` 与 `spatial_reconstruction.camera` 同一套。正对平面四角 UV 中心为 (0.50, 0.50)。
- `SpatialProjectiveTexturingTests` 三例在 iPhone 17 模拟器通过：正对中心、相机背后拒绝、同 epoch 正对比斜视优先；未观测面不贴图。
- 预览从固定 225 点高改为 `aspectRatio(3/4)`、`maxHeight 460`。

## 看图

对照 `docs/evolution/figures/2026-10-12-projective-texture/look-gate.png`（四格连环画，不是 App 截屏）：

1. 先对着墙拍照。
2. 墙的四个角落到这张 JPEG 里。
3. 斜着回看，墙上贴的还是这张照片（窗在上、门在左）。
4. 墙在相机背后就不贴，避免贴错房间。

预览从 225 点加高是另一处 UI 改动，不再画进这张图。

「原始平面 vs 贴图」用真实门墙照片三格模拟：`docs/evolution/figures/2026-10-12-projective-texture/plane-vs-texture.png`。

## 未验证 / 不做

- 真机 SceneKit 里 UIImage 的 V 轴是否还要再翻一次。模拟器没有 AR 平面和 JPEG。
- 斜视拉伸、多平面抢同一张照片、曝光未锁时的色差。
- 物体轮廓立牌。
- 平面融合；ARKit 原始多边形不变。

## 实现要点

- 同 epoch、≥50% 顶点在画面内、全部在相机前方；得分 `visibleFraction * (0.25 + 0.75 * incidence)`。
- JPEG 尚未写完时保持半透明色，文件出现后再贴，避免把失败缓存成「这面没颜色」。
- 直播叠层透明度 0.28，回看不透明；线框 `renderingOrder = 10`。

更新时间: 2026-10-12 18:30
