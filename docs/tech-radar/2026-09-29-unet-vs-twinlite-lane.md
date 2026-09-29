# 技术雷达：经典 UNet vs TwinLite 车道线（速度 / 准确性）

- 日期：2026-09-29
- 小马结论：**L1 学习更新** — 不建议用经典 UNet 替换 TwinLite 主航道；速度几乎必输，准确性无公开同条件胜出证据
- 相关角色：乔布斯 / 罗根 / 全麦 / 思余
- 触发：用户问「UNet 比 TwinLite 在车道线上速度和准确性会更好吗」

## 1. 来源与可信度

| 来源 | 类型 | 可信度 | 备注 |
| --- | --- | --- | --- |
| [TwinLiteNet arXiv:2307.10705](https://arxiv.org/html/2307.10705v5) | 论文 | 高 | 明确把 UNet/SegNet 等标为算力过重；BDD100K 双头分割 |
| TwinLiteNet+ arXiv:2403.16958 | 论文 | 高 | 更大配置可抬高 lane IoU，仍属 TwinLite 族 |
| VQASee `docs/CURRENT.md` | 产品现状 | 高 | TwinLite 为默认路面/车道；下一阶段几何是 UFLDv2 |
| 经典 UNet（Ronneberger 2015） | 医学分割原论文 | 高（方法） | 通用 encoder–decoder + skip，非驾驶车道专用评测 |

## 2. 核心认知

### 2.1 不是「有 skip 的 UNet」对「没 skip 的 TwinLite」

- TwinLite：**ESPNet-C 编码器**（膨胀卷积多尺度）+ Dual Attention + **双解码头**（可行驶区 / 车道），~**0.4M** 参数；论文报告 A5000 ~**415 FPS**，Jetson Xavier NX ~**60 FPS**；BDD 车道 IoU ~**31.08%**（与 HybridNets 同量级）。
- 经典 UNet：对称编解码 + **concat skip**；医学/通用分割强，但论文语境里正是「算力贵、难上嵌入式」的那一类。
- TwinLite 用 **ESP 多尺度 + 双头** 解决「像素级车道」需求，机制不同，**不能**说 UNet 因为有 skip 就一定更准。

### 2.2 速度

| 判断 | 依据 |
| --- | --- |
| **经典 UNet 通常不会更快** | TwinLite 设计动机就是替代 UNet 级重分割；参数量与解码路径更轻 |
| VQASee 含义 | 本地叠层要吃实时预算（~5 Hz 感知间隔）；换重 UNet 更可能砸延迟，不是提速 |

### 2.3 准确性

| 判断 | 依据 |
| --- | --- |
| **没有「UNet 稳赢 TwinLite」的同条件公开表** | TwinLite 论文主比 YOLOP / HybridNets 等，不是同设置 UNet ablation |
| 理论上大容量 UNet 可能抬 pixel IoU | 代价是算力；且产品车道验收已转向**折线几何**，不是 CamVid 像素 IoU |
| 产品下一杆 | CURRENT：**UFLDv2 行锚折线** > 再抠经典 UNet mask |

## 3. 分角色翻译

| 角色 | 含义 |
| --- | --- |
| 乔布斯 | 不要为「UNet 更经典」换默认；用户要的是户外实时车道边，不是架构名 |
| 罗根 | 默认假设：换经典 UNet → p95 变差；要换必须真机 Core ML 计时 |
| 全麦 | 要比就同数据同分辨率同导出；否则数字不可比。更优先 UFLD/TwinLite+ 族 |
| 思余 | 叠层源不变则 UI 无感；换模必须防静默失败与「无折线」状态 |

## 4. 最小实验（若坚持对照）

1. 同 BDD/自有街景子集，同输入尺寸，导出 Core ML。
2. 指标：lane 容差召回/精度 + 真机/harness p50/p95。
3. 退出：速度不优于 TwinLite **或** 产品折线视觉不过乔布斯看图门 → 停，不替换主航道。

## 5. 采纳等级

**L1**：认知写入雷达；**不**改 App 默认。准确度要抬，优先几何路线（UFLD）或 TwinLite+，不是回退经典 UNet。

更新时间: 2026-09-29 13:36
