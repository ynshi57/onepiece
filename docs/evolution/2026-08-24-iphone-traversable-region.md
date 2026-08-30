# 进化记录：把「iPhone 感知的可走区域」拉进闭环（2026-08-24）

## 触发

用户在逐帧页看到 CamVid 真值绿区后提出：三区方块“好无意义”，iPhone 感知“用的是模型还是算法？”，并给出目标——“**我的目标是 iPhone 感知出绿色可行走区域**”。

## 四角色

- **乔布斯（产品）**：用户要的不是 3 个状态格子，而是“端上把可走区域看准”。方向：让 iPhone 感知的可走区域**看得见 + 评得出 + 上门禁**，形成“感知 → 对标真值 → 量化差距 → 喂回模型”的闭环。区域 IoU/recall/precision 作为验收轴。
- **罗根（系统/性能）**：分割每像素图物化很贵，端上实时预算不能碰。裁决：导出**opt-in**，只在离线 harness 打开；在线 App 延续“只采 ROI + ≤16 行中心线”。
- **思余（UI）**：两块都绿、分别开关，默认显示 iPhone 感知区域;粗网格 `pixelated` 保留诚实分辨率;内联 data-URI 不加请求。
- **全麦（模型/评测）**：结论——iPhone 感知是**模型（分割）+ 算法（ROI 降维）**，模型算了却被丢弃。补上导出 + 区域 IoU 评测 + 门禁；实测暴露模型在城市街景偏弱（mean IoU 0.625，个别帧 0.00，把天空当可走）。

## 落地

- Swift：`TraversableGrid` + `emitTraversableGrid`（opt-in）+ harness 输出 `traversable_grid`。
- Python：`app/region_grid.py`（下采样/评分/聚合/门禁）；manifest 存 GT 网格（单一真源）；诊断台聚合页“可走区域指标”卡组 + 逐帧页“iPhone 感知区域”绿层与开关；`run_ios_harness_eval.py` 接入区域 baseline/gate。

## 指标（CamVid 701）

mean IoU 0.625 · recall 0.645 · precision 0.804 · false_go 118 帧 · miss 158 帧。

## 验证

- `swift build` 通过；重跑 701 帧全部带网格。
- `pytest server-vqa/tests` **233 passed**（新增区域单测 13 + API 4）。

## 未做 / 下一步（明确标注）

- **本轮未改模型**：只把真实感知导出、量化、可视化、上门禁。抬高 IoU/recall 需动模型（更强可走分割 / CamVid SFT/蒸馏 / 天空抑制），已进 backlog，全麦主责，验收用本轮 region 门禁。
- 最小实验先行：扫 `segTraversablePixel` 阈值看 recall/precision 折中（不改模型）。

## 续（Phase 1，2026-08-24 晚）：换成户外权重，门禁立刻兑现价值

上一轮把"模型偏弱"变成可回归事实后，本轮据此做模型替换（详见
`docs/model-lab/2026-08-24-cityscapes-fast-scnn-swap.md`）：

- 根因确认：端上是**室内地板** Fast-SCNN（`fast-scnn-floor-segmentation`）跑户外，域不匹配。
- 换成 **Cityscapes** 版 Fast-SCNN 权重（Tramac，GitHub 直下），加 19→2 通道归约头
  （可走 = road+sidewalk）+ ImageNet 归一化，**端上 Swift 契约不变**。
- 结果（CamVid 701）：IoU 0.625→**0.774**、recall 0.645→**0.793**、precision 0.804→**0.973**、
  安全关键 false_go **118→0**、miss 158→83。上一轮建的 region 门禁在这一轮直接量化出收益。

经验补充：

- **"模型没救"往往是"权重喂错域"**：Fast-SCNN 架构本就是 Cityscapes 出身，换权重就好，别急着换架构或上多模型切换。
- **先建门禁、后换模型**：因为有了 region IoU/recall/precision + gate，这次替换不是"看着顺眼"，而是有 118→0、+0.15 IoU 的硬证据，且能防未来回退。
- **契约优先于权重**：把 19 类归约进既有 2 通道契约（logsumexp → sigmoid 等价 softmax），让端上一行不改就吃上新模型——共享契约稳定，blast radius 最小。

## 续（Phase 2，2026-08-25）：CamVid 微调，证明闭环能修漏报

详见 `docs/model-lab/2026-08-25-camvid-fast-scnn-finetune.md`。

- 无泄漏设计：CamVid 是视频，按序列难度定"带 gap 的时序留出"——0016E5 尾段 107 帧留出、
  头段+其余序列进训练。留出帧永不参与训练。
- 方法：从 Cityscapes 权重初始化、warm-start 2 类头、Tversky(β>α) 专抬 recall、CPU 8 epoch 早停于 6。
- 结果（留出集）：IoU 0.495→**0.968**、recall 0.496→**0.992**、漏报 50→**0**、precision 0.996→0.976（false_go 仍 0）。
- 导出保真：CoreML vs torch `max|Δprob|=0.0149`。

经验补充：

- **留出集选错会测不出价值**：先按序列量难度，发现 83 漏报里 73 在 0016E5、而 Seq05V 是 0——
  若把简单序列当留出集，微调再有效也"看不出来"。**先诊断难度分布，再定 held-out**。
- **专化 ≠ 泛化**：CamVid 微调在 CamVid 评测大胜，但不代表真机户外更好；本轮交付的是"配方被证明"，
  不是"该模型应上真机"。是否上默认交乔布斯裁决，真正正解是**用真机帧微调**。
- **有了门禁+留出，模型改动才敢下结论**：0.495→0.968 是硬证据，不是错觉。

## 续（2026-08-25）：退役 near/left/right 三区 ROI 框

详见决策 `docs/decisions/2026-08-25-retire-three-roi-boxes.md`。

- 用户判断「三个框没用」方向对：即时语音走 YOLO 方向、引导线/可走区域从整张 mask 独立算，都不消费这三个框。
- 但它还偷占两处：设备浮层在画方框、且是闭环**主门禁**（status_accuracy≈0.44）。所以按**迁移**做，不裸删。
- 本轮已改并验证：诊断台逐帧去三框/去三区表、harness 不再输出 `roi`、主门禁改锚到引导线+区域、退役
  `camvid-ios.json`、设备浮层删方框/状态 chips。`pytest 236 passed` + `swift build` 绿；`CameraRiskOverlay`
  需 Xcode 复核。
- 分阶段延后（需重活/需 Xcode）：案例层改锚到区域失败、删干净 Swift 引擎 ROI 字段、清 manifest 真值 ROI（需 701 帧重生成）、下线 config 的 ROI 编辑器。

经验补充：

- **「没用的降维信号」往往还在暗处当门禁**：删之前必须按语义查消费方（谁 gate、谁渲染、谁间接喂 Qwen），
  否则修好一个入口、在门禁处静默塌陷。先把价值搬到新信号（引导线+区域）再拆旧的。
- **改动能验证多少就诚实标多少**：server/harness 能 pytest + swift build，App SwiftUI 文件不在 harness 目标里、
  本机无完整 Xcode，就明确标「已写未编译验证」，不把没验证的当验证过。

## 经验

- 用户说某降维输出“无意义”，常意味着**上游更丰富的中间图被算了却没暴露**；原样画出来（哪怕难看）比美化降维更有价值。
- 闭环升级的正确形态：不是加一张图就完，而是**看得见（UI）+ 评得出（IoU/recall/precision）+ 挡得住（gate）+ 存得下（manifest 单一真源）**，让“模型偏弱”成为可回归、可验收的事实，而非一次性观察。
