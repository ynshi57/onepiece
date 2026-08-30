# 技术情报卡：行人可通行区感知的 2026 SOTA（对 VQASee 的启示）

日期：2026-08-26 · 主责：小马 · 触发：用户问「二值角色盲真值行吗，有没有更好方案」
影响角色：乔布斯（产品/数据/验收）、罗根（端上/深度融合）、全麦（模型/数据/指标）、思余（呈现）

---

## 1. 结论（TL;DR）

VQASee 现用的**二值、角色盲**真值（可走 = 马路∪人行道）**作为起点能用、作为终态不行**。2026 SOTA 明确把行人助行感知做成**多类、安全导向、深度感知**。我们规划的「角色条件多类分割 + 车道线 + 障碍物」方向**被 SOTA 背书**；但有三个更优方案应纳入 roadmap：pedestrian-view 数据、深度融合、SAM2 自动标注。

## 2. 关键来源（可信度：顶会 / arXiv / 活跃开源）

| 来源 | 是什么 | 对我们的价值 |
|---|---|---|
| **SENSATION-DS**（arXiv 2607.21137, 2026）《Safety-oriented sidewalk and road segmentation for smartphone-based assistive navigation》 | 手机端行人助行分割框架 + 数据集（2752 张胸高行人视角、9 类导航 taxonomy） | **和我们同题**。确立安全指标 **Road-as-Sidewalk Error Rate**（把马路误当人行道，false-safe 代理）；实证 **SAM2 伪标签降低该错误**；端上基准 DeepLabV3+-MobileNetV3 错误率 0.079、UPerNet-MobileNetV3 mIoU 0.715、512×384 @ 7.4 FPS(Android) |
| **WalkGPT**（CVPR 2026） | pixel-grounded VLM，**depth-aware segmentation** + 对话式行人导航；PAVE 基准 41k、基于 SANPO | 证明「语义 + 深度 + 可达/有害特征」是行人导航正解；可作对标，避免重复造 |
| 单目度量深度：**Depth Anything V3 / UniDepth V2 / Metric3D V2 / Depth Pro**；端侧 **ZipDepth / METER** | 零样本度量深度基础模型 | 加「深度」这一信号；iPhone Pro 有 LiDAR/ARKit 原生深度可直接用 |
| **Grounded-SAM2 / SAM2Auto / Autodistill** | 文本 prompt → 开放词表检测(Florence-2/YOLO-World) → SAM2 像素级掩码，自动标注 + 蒸馏 | 突破 CamVid 固定类/701 帧，自动造「sidewalk/crosswalk/curb」角色真值 |

## 3. 对 VQASee 各角色的翻译

- **乔布斯**：用户是行人 → 数据视角必须从「车载引擎盖」转到「胸高行人视角」;**Road-as-Sidewalk Error Rate 就是产品级安全验收指标**（把马路说成能走 = 信任崩塌）。WalkGPT 几乎是我们的目标形态，值得研究对标。
- **罗根**：端上多类分割可行（DeepLabV3+-MobileNetV3 / SegFormer INT8 ~50–100ms）;**iPhone 原生深度（ARKit/LiDAR）可直接融合**，省一个深度模型，是我们独有优势。深度 + 语义融合的延迟/内存预算需实测。
- **全麦**：多类分割 + Road-as-Sidewalk 门禁 + SAM2 自动标注扩数据 + 蒸馏到端上小模型;深度作为第二信号解决 curb/step（纯语义漏）。
- **思余**：WalkGPT 的「可达特征(绿)/有害特征 + 相对深度」呈现范式可借鉴，保持安静分层。

## 4. 对当前 Phase A 的影响

**不推翻，反而加强。** Phase A（角色条件真值 + 4 指标）照做；把 A3 的「马路被误当首选率」**对齐 SOTA 口径**命名/定义为 **Road-as-Sidewalk Error Rate**，并在评测里作为 false-safe 安全指标。三个更优方案写进 roadmap 后续 Phase（见决策文档）。

## 5. 最小可验证实验（成功指标 / 退出条件）

1. **[Phase A 内] 指标对齐**：A3 落地 Road-as-Sidewalk Error Rate（成功：能算出当前二值模型对行人的该错误率，量化缺陷）。
2. **[Spike-深度] ARKit 深度融合**：取几帧 iPhone 深度 → 简单地面/障碍几何 → 和语义融合，验证 curb/台阶能否被检出（成功：curb 帧上深度信号显著；失败退出：无 Pro 设备/深度噪声过大则记录并转纯语义）。
3. **[Spike-自动标注] Grounded-SAM2 伪标签**：在 10–20 张真机胸高帧上 prompt "sidewalk/crosswalk/curb"，人工核对掩码质量（成功：多数掩码可用 → 建自动标注管线;失败：prompt 不稳 → 回退 CamVid+微调）。

## 6. 风险 / 待验证

- pedestrian-view 数据获取（SENSATION-DS/SANPO 许可与可得性）待查。
- 深度融合仅在 iPhone Pro（LiDAR）最佳;非 Pro 设备用单目深度需端上模型，成本上升。
- SAM2/Florence-2 自动标注是**离线造数据**用，不是端上实时（端上仍是蒸馏后的小模型）。

## 7. 沉淀去向

- 决策文档 `docs/decisions/2026-08-26-role-conditioned-traversability.md` roadmap 增补 Phase D（数据视角/深度/自动标注）。
- A3 指标命名与 SOTA 对齐（代码 + 测试）。
