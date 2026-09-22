# 感知层四问（乔布斯裁决）

## 最终结论

1. **YOLO 不能检台阶/路沿**，不加。
2. **不能把 TwinLiteNet 直接当可走区+车道线产品默认。** 无标注叠图里它比 UFLD 好看，12 帧没过门，也不分人/车。
3. **live 二值 Fast-SCNN 已退役**（有害 + 延迟大头）。打开 App 暂时没有绿可走区和分割中线，还剩 YOLO 框 + 车道像素。
4. **Mac≠iPhone 能解**：先删大头；别用 Intel PyTorch 2.4s 判 TwinLiteNet 实时；mc5 要真机 ANE。

## 投票

GPT / Sonnet / Gemini / 乔布斯赞成。子代理额度不足，审阅按清单写出。

## 已执行

- `LocalVisionAnalyzer` live：`useMulticlassSegmentation=false` 时不加载二值模型
- harness 注入的 segmenter 为 nil 时不再回落到 binary
- Python 配置注释对齐

## 待办 / 阻塞

- App 改动未在本机用完整 Xcode 编译（未验证 / 待 Xcode 复核）
- TwinLiteNet 不过门不上 App
- 台阶/路沿需要分割边界或专用模型，不是给 YOLO11n 加两个字符串

更新时间: 2026-09-21 12:10
