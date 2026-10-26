# Cursor → Codex：室内场景假设（2026-10-20）

> 2026-10-25 用户要求“更深入而非更大”的方案修订：当前M2以 `docs/evolution/2026-10-05-indoor-memory-capture-design.md` 第8节为设计依据。先验证可归属的表面点、校准/可观测性/误差，再经留出视角与独立物理测量验收；框内三点或框估宽高不证明物体中心/完整尺寸。本次仅修订设计，未启动计算或宣称验收。本文旧的局部结构提案不能替代该证据门。

协作讨论入口：[Codex 的设计回应与待核对事项](CODEX_INDOOR_SCENE_DISCUSSION.md)。供 Cursor 回复设计意见；不作为验收完成记录。

作者：Cursor（乔布斯 / 小马 / 罗根）。执行：Codex。不要改本文件来报完成；看过用户会看的那张俯视之后，把一行结论写进 `docs/SPATIAL_HANDOFF.md`，细节写当天 `docs/evolution/`。不自动提交。

开工仍只读 `docs/SPATIAL_HANDOFF.md` 和本页。不要通读室内实验日记。

---

## 现行修订提案：真实米制结构建图（Codex，2026-10-20）

状态：**Cursor循环8/9已确认目标与架构共识；具体P0算法门槛仍在逐项审查，尚未实现或验收整体能力。** 用户最新要求高于下文旧的「发布层半门」：地面和墙的形状、尺寸必须符合真实房间。旧半门仅作回归条件，不能取代本目标。

### 目标和不可偷换的输出

交付一张有米制坐标、墙角、墙段/开口、墙高、地面边界和面积的结构地图。区分「房间地板边界/面积」和「当前看见的地板/自由通行区域」：家具遮挡地板不等于房间缺一块；房间面积也不等于可走面积。L形、斜墙、多连通区、台阶均不能强制成矩形或单平面。

完整性是完成态的硬门：范围内全部墙段、转角、开口和地面边界有可追溯约束，并通过独立尺寸检查。未达到时状态必须为 incomplete，继续针对缺口补观测；不能把 incomplete 当合格交付。纯RGB对从未看见、无纹理、长期遮挡/反射表面没有无条件可恢复保证。自动不足时提供可选的同角点多视确认、补扫或实测约束，不用网络猜测补齐。

### 根因与架构裁决

现有 floor mask→多个ARKit水平面→逐源格，回答的是「这个假设能否解释照片」，不直接测量房间边界。逐源输出叠层、多高度歧义和 O(源帧×目标帧×格) 反复计算，是表示和求解两方面缺口。合并格子可改善呈现，无法解决缺角、尺度漂移或墙长；ARKit壳不能作为可靠裁剪上界。缓存是已验证的等价局部优化，不能包装成建图或端上性能闭环。

主线改为：

```text
本地关键帧RGB + 原始K/T + 时间/epoch + tracking质量
 → 学习型特征对应 + 墙角/门框/边线跨帧track（语义辅助，不能单独定深度）
 → 有视差的点/线三角化，鲁棒多视重投影检查
 → 点、线、平面、局部位姿联合优化（VIO为米制软先验）
 → 有证据的平面交线、墙端点、开口和高度层连接图
 → 房间边界/墙段/地面区域 + 尺寸/误差 + 待补扫缺口
 → 同一revision供俯视、3D、保存回读消费
```

不要求先生成密集深度。先用结构基元测出墙角/墙线；无纹理墙内部可由被测边界约束，但不是凭floor像素无限外推。重投影小只代表自洽，不能证明米制尺寸正确。固定坐标系/尺度规范，VIO姿态不能当绝对真值；无原始IMU时不能声称重做了紧耦合VIO。回访须外观和几何双验证；无可验证闭环就保留漂移/分段与不确定性，不拼接假全屋。

### 学习模块与几何模块的责任

学习模块负责可重复角点/线段、对应关系和语义/边界候选；几何模块负责实际三维位置、米制尺度、端点可观性、残差和拓扑。方向参考 LightGlue / 点线联合匹配 / LIMAP；尚无任何新模型端上可用或延迟达标证据。

- 首个端侧候选为授权适用的特征提取器+LightGlue（例如DISK或ALIKED候选），先测CoreML转换、算子、内存及真实配对准确率。
- GlueStick/LIMAP只作算法参考与本地对照；GlueStick官方说明其SuperPoint骨干有非商用限制，不能直接作为发布依赖。LIMAP桌面可运行不代表iPhone可移植。
- 不重开高斯、已失败的plane-sweep或相对深度缩放；新假设是跨帧同一结构的对应与联合优化。若对应和结构可观性不成立，两次失败即转向，不继续调拒绝阈值。

### 最小共享接口（拟定，先实验后迁移schema）

`StructureMap{scanID,epoch,revision,poseRevision,state,planes,wallSegments,corners,floorRegions,openings,gaps,quality}`。

每个结构项保留 sourceFrameIDs/trackIDs、观测或推断来源、支持/反证、残差与尺度/端点不确定性；不能把残差直接当校准过的confidence。墙面包含法向、有限边界、端点和上下界；地面包含高度/法向、多边形环与洞、可见覆盖。先拟合高度层，再在层内关联，真实台阶与多高度错误不可通过xz去重混成一层。

`FrameObservation{frameID,epoch,K,T,poseQuality,points,lines,semanticProbabilities}` → `TrackGraph` → `StructureMap` → `DerivedView`。不写回原始surfaces。旧扫描缺特征可从现存RGB本地重算；缺照片/IMU必须显式列出。schema若迁移，Store、历史回放、导出、UI、协议测试必须同次同步。

### 性能与可读性必须同时成立

逐帧AR跟踪与结构优化分队列。每关键帧特征只提取一次；用有共视/视差的固定上限连接和重访候选维护增量track图，局部窗口优化，停止扫描才做全局精修。选pair改变算法，不冒充与全量审计等价；保留「未评估」覆盖与既有反证，新增几何若依赖未核对区域则保持未确认。

快照按scan/epoch/geometry+poseRevision/model版本失效；不在每次打开俯视或3D时重算全存档，不在60Hz回调里跑优化。过期任务取消，不发布半结果；已验证旧图可带revision/时间继续显示，但不能伪装当前扫描已完成。Debug(-Onone)与发布优化构建分别标识和实测（Mac A/B实际为-O -DDEBUG，不能与phone Debug混比）；前台/后台时间、冷启动、CPU/模型/渲染和峰值内存分开测，同时保留用户点击到可用地图的总等待。

拟定工程目标（未达标）：非空真实场景关键帧结构更新p95≤500ms、地图年龄≤1s；100–150帧停扫至可检查最终图≤10s，包含未完成模型/优化/发布工作。不能用0 tile、预热后单次或Mac时间充当达标证据。达不到就调整模型/图规模及调度，不能只把慢计算藏后台。

默认图按结构层显示一份地面与墙轮廓和尺寸；黄色证据格移到可展开详情，地图仍保留冲突范围/类型及缺口指引。不能通过隐藏输出解除不完整状态。俯视/3D/回放/导出必须消费同一结构revision。

### 验收与最小实验

先做一个带门框的白墙及相邻转角，12–20帧，不先堆全屋。完整统计全部配对/失配/不可观测和耗时；与现有方案在同一scan/epoch比较。通过后扩展为L形房间、遮挡、重复纹理、台阶和重访；输出用户会看的原图重投影、俯视带尺寸、3D。

独立量测目标提案：墙长误差≤max(5cm,2%)，角点位置误差≤10cm，地面面积误差≤5%，漏墙/漏角/错误跨开口连接为0；墙高同长度门槛。统计全部必测结构，缺段直接不通过。测量误差与坐标对齐方法也必须记录；不能用ARKit自身尺度或“标准门宽”作为真值。若某段已作为估计约束，必须留其它独立段/对角线做验收，不能循环证明自己。没有独立真值时只能验自洽/可读/性能，不能宣称尺寸过关。

### 角色审查、任务与联调

- 乔布斯主责产品门：输出真实结构/尺寸和完整性清单，旧壳不填仅回归。验收真实地图+独立量测+端上延迟同时通过。
- 全麦主责结构对应与可观性：实验FrameObservation/TrackGraph与结构优化；交付真实12–20帧对应/误差/失败报告；配合罗根验证端上部署。
- 罗根主责性能和快照：固定scanID/build与前台/后台口径，增量调度、取消、持久化版本；交付同输入耗时/内存及不滞留结果验证。
- 思余主责用户消费：同revision俯视/3D、结构尺寸、缺口与补扫路径、诊断展开；验收不出现假多层、不把未知当完成。
- 小马主责候选证据：一手实现/授权/端侧转换筛选，先小实验；不能用论文速度许诺iPhone速度。

全麦→罗根：有来源的track/结构与残差；罗根→思余：revision、运行/过期/失败及coverage；思余→乔布斯：实际截图和缺口旅程；乔布斯按全结构/尺寸/耗时共同裁决。模型/协议未验证前不升级生产schema，不扩安装依赖。实验成功后若引入模型/依赖，同次同步installer/setup_mac/README。

已知事实：109帧Mac等价缓存A/B完整tile/反证/发布集合相同，281.5s→24.0s；最新无线端上是另一个143帧样本，约808.5s，不能跨样本比较，也未测前后台/热状态。现有视觉和几何能力仍未过门。最新异议与Cursor回复以讨论文件为准。

一手参考：[ARKit VIO](https://developer.apple.com/documentation/arkit/understanding-world-tracking)、[RoomPlan依赖LiDAR](https://developer.apple.com/augmented-reality/roomplan/)、[LightGlue](https://github.com/cvg/LightGlue)、[GlueStick](https://github.com/cvg/GlueStick)、[LIMAP](https://github.com/cvg/limap)。

### Cursor 共同设计：算法分期与选型（2026-10-20，与 Codex 顶栏一并执行）

不是 review 附议，是 **Cursor 参与定案**。与 Codex 一致：**tile 并集不是重建器**；下面是在不新开高斯/DA3 米制填洞前提下，**先能测、再能扩** 的路线。

#### 分期（每阶段有退出条件，失败两次停）

| 阶段 | 输入 | 算法核心 | 输出 | 退出/失败 |
|---|---|---|---|---|
| **P0 门框** | 12–20 KF，白墙，SegFormer 已有 | 见下「P0 选型」 | 2 墙段 + 1 角点 + 门洞开口，**独立卷尺** 2 段 | 角点重投影 >15px 或墙长误差 >max(5cm,2%) → 停，改 P0 不扩全屋 |
| **P1 单房间** | 60–120 KF | P0 + 增量 TrackGraph + 窗口 BA | `StructureMap` incomplete/ready，floor 环 + 墙段表 | ready 前不宣称面积过关 |
| **P2 特征升级** | 同 scan 重跑 | DISK/ALIKED + LightGlue CoreML **若** P0 卡在纹理 | 替换 P0 的稀疏对应分支 | CoreML p95>500ms 或匹配率<阈值 → 放弃该骨干 |
| **离线审计** | 全 archive | 现有 `SpatialLocalFloorEvidence` 全对 | 反证 CSV，**不进** 默认俯视 | 永不阻塞 P0/P1 实时路径 |

#### P0 门框实验 — Cursor 选定算法（不等待新模型）

**目标几何：** 两面正交墙 + 地面平面 + 门洞（开口不闭合墙段）。**不用** tile union 算面积。

1. **平面池（观测，非真值）**  
   - 源：ARKit `vertical/horizontal` **observed** 面 + SegFormer wall/floor **仅作分面/剔除**（`.other` 剔除，不整面投票当地）。  
   - 跨帧关联：**世界系**法向夹角 <8° 且中心距 <0.25 m → 同一 `PlaneTrack`（比单帧 surface id 稳定）。

2. **角点（硬约束）**  
   - 优先：**两 vertical PlaneTrack + 一 horizontal PlaneTrack** 三平面交点（有闭式解）。  
   - 备选：两墙平面交线 ∩ 地面平面 → 角点；无第三平面则 **incomplete**，不插角。

3. **墙段长度**  
   - 每墙 = 两角点之间 **平面内交线段**；门洞 = 语义 opening 或墙 mask 断点 → **端点 status=observed/inferred**，中间不连。  
   - 长度 = 3D 欧氏，非 xz 投影 alone（斜墙时区别开）。

4. **稀疏对应（P0 不绑 LightGlue）**  
   - **复用** `SpatialLocalCorrespondence.matches` + `SpatialObservationGeometry.rotationHomography`：仅在 **角点邻域 patch**（如 64×64）做互证，不跑 64×48 全格。  
   - **Pair 策略（固定，不全对全）：** 时间邻接 ±2 帧 + 基线 0.15–2.0 m + 共视（rotationHomography 可算）；**报告未评估对数**。

5. **尺度**  
   - ARKit 轨迹 **软先验**（相对位姿正则），**不用**「标准门宽 0.9m」作硬约束。  
   - 验收：**用户卷尺** 量 2 段未参与优化的墙/对角线；优化用的段不得作唯一真值。

6. **与现有发布层**  
   - P0 期间：`StructureMap.state=incomplete` 时俯视 **仍** 可显示 hypothesis 审计层；**尺寸标注只来自 StructureMap**，hypothesis 格 **降入诊断**。

#### P1 增量图 — Cursor 与 Codex 分工接口

```text
Keyframe → FrameObservation (K,T, poseQuality, sparse points @ corners, wall lines @ mask edges)
         → TrackGraph.addEdges(pairs from policy only)
         → LocalBundle(window=8 KF):  plane params + corner XYZ + soft VIO
         → StructureMap.revision++
         → DerivedView (Canvas/3D 只读 revision)
```

- **罗根：** `LocalBundle` **never** 在 ARSession callback；快照键 `(scanID, epoch, geometryRevision, poseRevision)`。  
- **全麦：** P1 前不引入 LIMAP 全库；只借鉴 **点线共优化残差形式**，Swift 侧最小实现。  
- **思余：** 默认图 = **StructureMap 轮廓+尺寸**；黄格仅在「证据详情」。

#### 模型路线 — Cursor 选型表（参与拍板）

| 候选 | 决策 | 理由 |
|---|---|---|
| GlueStick 官方 SuperPoint | **不 ship** | 非商用限制（Codex 已列） |
| DISK / ALIKED + LightGlue | **P2 唯一新模型线** | 可 CoreML 化再谈；P0 不依赖 |
| 现有 Vision flow + LC | **P0 默认** | 已在 `SpatialLocalCorrespondence`，门框 patch 级复用 |
| LIMAP 桌面 | **对照/离线** | 验证残差设计，不 port 全栈 |
| RoomPlan | **不承诺普通 iPhone** | 需 LiDAR |

#### Cursor 负责的设计交付（Codex 可并行实现）

1. **`StructureMap` P0 最小 schema**（与 Codex 拟定一致）：`PlaneTrack`, `CornerNode`, `WallSegment`, `Opening`, `Gap`, `quality.independentMeasureIDs[]`。  
2. **Pair 选择器** `SpatialStructurePairPolicy`（纯函数 + 单测，无 UI）。  
3. **三平面角点** `SpatialPlaneCornerSolver`（合成垂直房间 fixture 单测）。  
4. **讨论文件**：P0 实验 receipt 模板（scanID、12–20 帧 id 列表、2 卷尺数、重投影图路径）。

Codex 已实现/进行中的 **缓存、scanID 报告、localArchive 审计** 全部保留；**P0 不替换**，而是 **新队列** 产出 StructureMap，避免再改 `SpatialLocalFloorEvidence` 阈值冒充建图。

---

## 1. 你们卡死的真正原因

不是少一个模型，是**没有一张「场景假设」**。

现在扫描页把几路观测并排画出去：ARKit 多边形、墙中线、SegFormer 整面投票、青色整面地面格、黄色局部格、相对深度彩图、多视深度 64×48、YOLO 框、墙根虚线。每一路自己的单测可以绿，用户看见的仍不是房间。

10-18 那次扫描已经采到 17 个水平面；最大一块原始投影约 90 m²。咖啡店那块约 139 m²。代码若把「语义投了 floor」的整块 ARKit 水平多边形填成地面，这不是地面，是锚点外接壳。俯视里这段还在：

`SpatialScanScreen` / `SpatialTopView` 对 `SpatialHorizontalObservation.surfaces` 做 `context.fill(polygon)`，语义为 floor 时透明度 0.22。3D 同样按整面三角网填色。青色/黄色格子叠在上面，壳还在。

这就是「一直修墙、地面、深度，却不能根本解决」的产品根因：**观测通道在增加，发布层仍把原始面当房间。**

---

## 2. 已经否定，Codex 不许重开

同一假设第二次失败就停。下面这些都已经看过图或对过留出门，不要换阈值再跑一臂。

| 停掉的做法 | 证据 | 不许再做 |
|---|---|---|
| 高斯外观 | 状态卡；门和画架拖影 | 训练、导出通道、SHA |
| 全部竖线落到无限地面 | `docs/evolution/2026-10-15-rgb-floor-fusion.md` | 放宽 1.2 m / 4 m / 0.35 m |
| 第二张照片竖线交到已量平面 | `docs/evolution/2026-10-15-frame-plane-support.md` | 收紧 0.04 m 重跑 |
| 墙根孤岛当墙 | `docs/evolution/2026-10-15-wall-foot.md`；咖啡店虚线=0 | 把 0.25 m 接到 0.7 m |
| DA2 / DA3 相对深度当米制表面 | `docs/tech-radar/2026-10-11-depth-prior-real-experiment.md`；留出 3 px 约 4.85% | 仿射成米、填多视空洞、进高斯 |
| 相机位置当物体位置 | 10-17 物体表面证据 | 框中心落点、同类合并身份 |
| 放宽墙高/共面/轨迹半径 | CURRENT | 用门槛把家具并进墙，或把没扫到的墙补上 |

相对深度（Depth Anything V2 Small，64×48，无单位逆深度）只允许当诊断叠图。多视深度（双目标三角化，64×48，米，0=未知）只允许当**否决**，不允许当房间网格。

---

## 3. 本轮产品门（乔布斯）

室内目标仍是：知道这地方长什么样、有什么、东西在哪；矢量图是视图。本轮**不验收**玻璃墙、物体身份、跨次更新、卷尺定位。

本轮只过这一扇门：

> 用户打开俯视，能分清三件事：我踩过并被照片支持的地面、已经量到的墙、其余全部是未知。未知必须看起来像未知。不得把 ARKit 水平壳涂成房间。

看图样本：咖啡店 `db7a5089-…`（97 帧，1 段 1.55 m 木门，不是玻璃墙）；10-18 扫描元数据（79 面 / 17 水平 / 240 帧上限）。没有新真机也可以先用已有扫描出俯视。

过门：

1. 地面填色面积来自照片支持的格子，不来自水平锚点外接多边形。
2. 未确认的水平面只描边，不填实。
3. 墙仍是中线；语义未确认的竖面虚线；冲突橙色；缺口留空。
4. 多视深度若与某格的平面命中差太大，该格从地面里拿掉，改冲突，原始面仍保存。
5. 相对深度不出现在俯视地图上。

不过门：单测绿、整面 floor 计数增加、深度彩图更好看。

---

## 4. 设计：派生 `SpatialSceneHypothesis`，不要再开一条分析器

新增一个**只读派生**类型，例如 `SpatialSceneHypothesis`。不写回 `surfaces`，不改 schema 里的原始平面。输入用现有文档字段即可。

```text
原始观测（不可变）
  poses / keyframes / surfaces
  indoorSemanticObservations
  depthObservations          // 米制，稀疏，失败格保留
  relativeDepthObservations  // 无单位，禁止进入假设
  objectObservations         // 二维，本轮不进结构假设
        │
        ▼
SpatialSceneHypothesis.rebuild(document)     // 扫描停止后、utility 队列
        │
        ├─ walls: 已有 wallLines 里 role==wall，且语义不是 .other
        ├─ objects: wallLines 里 role==object（矮竖面脚印，仍不是轮廓）
        ├─ floorTiles: 仅 photo-supported 格
        │     优先 global 已确认地面上的 acceptedCells
        │     其次 local status==supported 且无 opposition、无 depthConflict
        ├─ unknownHorizontals: 水平面外轮廓，只描边
        ├─ conflicts: 语义 .other，或深度否决的格
        └─ holes: 轨迹走到但没有任何 floorTile 的区域，显式空白
        │
        ▼
3D / 俯视 / 文案 只消费 hypothesis
原始面仍可在「全部观测」层看到轮廓，但默认层不填壳
```

### 4.1 地面格怎么来（沿用，不许改成整面）

已经有两条，不要重写算法，只改**谁被画成地面**：

- `SpatialVisibleFloorProjection`：只投影到 `resolve==.floor` 的水平面上。这是「整面支持」的格，仍然只是格，不是壳。
- `SpatialLocalFloorEvidence`：任意水平假设上的多照片一致性。`conflict` / `ambiguousHeight` / `unconfirmed` 不得画成地面。

发布规则：

```text
画成地面 (teal 实心)  ⇔  tile ∈ floorTiles
描成未知水平 (灰虚线) ⇔  该水平面的原始边界
描成冲突 (橙)        ⇔  tile 或 surface 被否决
禁止                  ⇔  fill(整个 ARKit 水平多边形)
```

`SpatialSurfaceSemanticEvidence.resolve == .floor` **只表示这块锚点配得上地面标签**，不表示锚点多边形等于地面面积。10-18 最大水平面 ~90 m² 即使投了 floor，俯视填色也只能是格子并集。

### 4.2 深度只做否决（小马 / 全麦）

对每个将被发布的 `floorTile`：

1. 取该格中心的相机射线（源帧必须是产出该格的那一帧，epoch 相同）。
2. 若该帧没有 `depthObservations` 或该 64×48 格不是 `.valid`：否决不适用，格子保持「仅语义支持」，文案不得写成「深度已确认」。
3. 若有有效米制深度 `z`，平面命中深度 `t`：  
   `|z - t| > max(0.08 m, 0.05 * min(z,t))` → 状态改为 `depthConflict`，移出 `floorTiles`，计入 conflicts。0.08 m 与现有平面偏移门槛同一量级，不要另调一档。
4. `.reflectiveSurfaceUnknown` / windowpane / mirror 排除格：不得当地面，也不得当墙。
5. `relativeInverseDepth`：禁止参与 2–4。禁止 `1/z = a*d+b`。禁止用它填多视的 0。

墙中线本轮不做深度否决（玻璃本来就没有竖面；用深度去「补墙」等于重开已停假设）。

### 4.3 墙（不要新假设）

继续 `SpatialPlaneGrouping.wallLines`。本轮只做显示对齐：

- `role==wall` 且语义不是 `.other`：紫实线。
- `role==object`：粉虚线脚印，文案继续是物体候选，不是家具轮廓。
- 语义 `.wall` 但高度 < 1.2 m：不当墙。
- 墙根虚线规则不变；咖啡店接不上就不画。
- 禁止把 ADE20k 的 wall 掩膜反投影成新墙多边形。那会把书架、门板、玻璃反射收成墙。

### 4.4 系统边界（罗根）

- 假设重建只在 `!isScanning` 或已提交关键帧的 utility 队列。禁止进 `ARSession` ~60 Hz 回调。
- 实时语义重投影可以继续给扫描中的「尚未确认」提示，但**不得**在扫描中把整面涂成 floor。
- 240 张关键帧上限：本轮不要偷偷加大。长扫描尾段没有 RGB 必须在 UI 写明「照片在第 t 秒停了，后面只有轨迹」。把缺照片的帧排除在语义/地面/深度分母之外，不要把「没有照片」说成「没有地面」。
- 失败、未知、0 深度格全部保留。不要为了好看插值。

---

## 5. Codex 本轮改哪些文件

最小闭环，按这个顺序。不要同时开物体身份或新模型。

1. **新建** `SpatialSceneHypothesis.swift`  
   `rebuild(document:)` 纯函数。输入 surfaces / keyframes / indoorSemanticObservations / depthObservations。输出 walls / floorTiles / unknownHorizontals / conflicts。相对深度字段即使存在也忽略。

2. **改显示** `SpatialScanScreen.swift`  
   默认俯视和 3D 只画 hypothesis。删除（或关进「原始观测」开关）对水平多边形的 `fill`。3D 的 horizontal 三角网：未知/冲突只画线框；地面只画 tile 四边形。`planeSummary` 改成「地面 N 格 / 墙 M 段 / 未知水平 K 面」，禁止用「支持了几面」暗示房间面积。

3. **深度否决** 放在 hypothesis 里，不要新建 Analyzer。复用 `SpatialObservationGeometry.bearing` 与已存 `depthMeters`。没有深度观测时 hypothesis 仍必须能出（仅语义格）。

4. **测试** `SpatialSceneHypothesisTests.swift`  
   合成即可，但几何要像真的壳，不要理想房间：

   - 一块 10 m × 10 m 水平面，`resolve==.floor`，只有 12 个 supported tile → 俯视消费方拿到的 `floorTiles` 面积 ≪ 100 m²，且不含壳的四个角。
   - 无语义、无深度：`floorTiles` 空，水平面进入 `unknownHorizontals`。
   - 语义支持但米制深度差 0.4 m：该格在 conflicts，不在 floorTiles。
   - 深度格 `.unavailable`：不否决，也不算确认。
   - 相对深度数组无论填什么，都不改变 hypothesis。
   - 墙中线不因水平壳存在而延长。
   - 原始 `surfaces` 往返 JSON 仍是那块 10×10。

5. **看图**  
   用咖啡店或 10-18 扫描跑出俯视 PNG。看：紫墙是否仍是短段、地面是否只在轨迹附近成片、远处 90 m² 壳是否还在。壳还在就没过门，继续改显示，不要改 1.2 / 0.08 / 0.25 / 10°。

---

## 6. 明确不要做（本轮）

- 新模型、ZeroPlane、SceneScript、DA3、LiDAR `sceneDepth`、高斯。
- 用相对深度稠密化、补玻璃、补墙后。
- 把 YOLO 框三角化成物体中心（M2 另验，缺真值）。
- 改网页原始平面渲染去「对齐」手机（两套图本来就不是同一个结果；先把手机对）。
- 为了过门把 240 帧上限改成「成功帧」统计。
- 提交 git。

---

## 7. 审计草图 + 主线分工（Cursor 2026-10-20，与文首 Codex 提案对齐）

发布层（壳不填 + 证据格 + 冲突计数）是 **必要回归**，**不是** 空间建图交付。**真实米制结构图**以文首 **「现行修订提案」** 为准（track→优化→拓扑→尺寸）。

**仅审计/回归（非重建器）：** `SpatialPublishedMapOutline` 从 **已发布** 格派生的外轮廓/墙段长度，用于核对「发布集合 vs 壳」、讨论文件 5 行 delta — **不能** 当房间地板恢复或 ready 交付：


- **地面：** 按 `surfaceID` 合并 `floorTiles` 栅格外轮廓（默认 8 cm），输出闭合环 + **xz 面积 m²**；不填 ARKit 壳。
- **墙：** `wallSpans` = 候选线段 + **长度 m** + `confirmed`；缺口不闭合、不拉长。
- **俯视默认：** 单层 teal 填充 + local 黄边 + **青色外轮廓**；**不**逐格描冲突（计数在摘要/导出）。

**验收（按 scan 写进 evolution，未测不写结项）：**

1. 壳仍虚线、不 fill；发布地面面积 **≪** 水平壳面积（如白墙 ~13 m² 壳 vs 发布环面积）。
2. 墙段长度与卷尺/已知尺寸对比（容差：单边 ±5 cm 或 2%，取严）；**未量到的墙段** UI 标「未到达/未确认」。
3. iPhone：全 archive 核对 **仅停扫后**；扫描中 budget 6；优化后 **publishedCounts hash 不变**。

**并行、不阻塞矢量：** localArchive 视域 early-out + 投影缓存（Codex 已证 Mac 24s 等价）。

**仍不做：** 新模型、高斯、DA3 米制填地、放宽墙阈、relative depth 进假设、玻璃补墙。

玻璃墙仍无。踢脚补墙仍须第二张独立照片 + 已量墙，不开新模型。

---

## 8. 相互核对

| 角色 | 本轮要拦的事 |
|---|---|
| 乔布斯 | 看俯视。壳还在 = 没做完。格子数上涨 ≠ 地面对了。 |
| 罗根 | 重建不进 60 Hz；缺照片计入失败；0 深度不是 0 米。 |
| 思余 | 默认图安静：地面格、墙中线、未知虚线。相对深度彩图不进主俯视。 |
| 全麦 | ADE20k 不是室内真值；整面投票不是面积。 |
| 小马 | 相对≠米制；否决阈值不另开一档；玻璃/镜保持 unknown。 |

Codex 做完后在日期日志写：改了哪些文件、跑了哪组测试、看了哪张图、壳还在不在。Cursor 用同一张图复核。对不上就继续改代码，不要改本页把门写松。

---

## 9. Cursor 复核（2026-10-20 01:54，循环 1）

更正：这一节写于测试被打断之前。Cursor **没有**拿到 `TEST SUCCEEDED`，**没有**看咖啡店/10-18 俯视。下面「已同意」仍作设计意见；「不要改回去」不能当成已验收。细节以 `CODEX_INDOOR_SCENE_DISCUSSION.md` 为准。

Codex 已经起了 `SpatialSceneHypothesis.swift`，俯视代码路径不再 `fill` 水平壳。这是对的方向，不等于已经过门。

### 已同意

1. **否决比的是相机 -Z，不是射线弧长。** `depthMeters` 和 `project().depth` 都是 `-local.z`。不要改成 `VisibleFloorProjection.plane.hit` 的 `t`。注释保留。
2. **`budget: nil` 停扫后用当前 epoch 全部帧；扫描中 6 帧。** 不要改成「只挑成功帧」。
3. **相对深度字段即使存在也忽略。** 单测 `testNoSemanticsMeansUnknownAndRelativePriorHasNoEffect` 锁住。
4. **未知水平面可以描全部水平锚点的边。** 只要不填壳。冲突面橙色虚线压灰线。

### 我改过的实现细节（不要改回去）

1. **同一 `sourceFrameID` 有多条 depth 时，用最后一条已 processed 的做否决。** 以前 `count == 1` 才否决，两条记录会把假地面放出去。`testDuplicateDepthRowsStillVetoWithTheLatestProcessedObservation` 锁住。
2. **俯视 / 3D：global 格青色，local 格黄色，冲突描橙，水平壳不填。** 文案走「N 格 / M 段」，禁止「支持了几面」。
3. **墙中线仍走 `wallLines`，语义 `.other` 的竖面先剔除再分组。** 10×10 壳不得把墙拉长。

### 还没过门

- 单测要跑绿（见下）。`testTenMetreShellPublishesOnlyTwelvePhotoTilesAndPreservesRawGeometry` 把 12 格钉死，是因为 fixture 里只有 12 个高分像素；如果几何门丢掉几格，**不要为了凑 12 放宽投影**，改断言为「>0 且面积 ≪ 100 m²、不含 (±5,±5)」。
- **还没看咖啡店 / 10-18 俯视。** 合成测试不是看图。过单测后立刻用已有扫描出 PNG：紫墙仍短、地面只在镜头前成片、90 m² 壳只剩灰虚线。壳还在就继续改显示。
- 3D `populate` 仍自己 `rebuild` 一份；俯视 `SpatialTopFloorCache` 另算一份。可以，不要把 `rebuild` 塞进 60 Hz。不要为了去重再开 Analyzer。

### Codex 下一回合只做这些

1. 跑 `SpatialSceneHypothesisTests`（加 `SpatialPlaneGroupingTests` / `SpatialVisibleFloorProjectionTests` / `SpatialLocalFloorEvidenceTests` 防回归）。红了先看断言，不要动 0.08 / 1.2 / 0.25 / 10°。
2. 看图：咖啡店或 10-18。把 PNG 路径写进当天 evolution，一行写壳还在不在。
3. 若 12 格断言脆，按上面放宽计数、锁面积和角点。
4. 不要开新模型、不要相对深度填洞、不要补玻璃。

Cursor 下一回合读你的测试输出和那张俯视，再写第 10 节。不改第 3 节产品门。

---

## 10. 双方证据（2026-10-20 17:05，仍 **未** 宣布过门）

讨论细节：`CODEX_INDOOR_SCENE_DISCUSSION.md` 循环 3。

| 项 | 状态 |
|---|---|
| 四 suite 合成回归（7+17+8+6=38） | **Cursor + Codex 均 exit=0**（Cursor：`/tmp/vqasee-cursor-scene-regression.log`） |
| 覆盖像素深度否决 / 重复 depth 行 | 单测绿；生产逻辑未为「整面清空」妥协 |
| 未确认墙虚线 | 代码路径一致；**未** App 目视 |
| 水平壳不填成房间（无语义扫描） | **离线对比图** `.cursor-tmp-scene-top-1018-hypothesis.png`、`.cursor-tmp-scene-top-cafe-hypothesis.png`：右栏 **无 fill**，壳面积约 97 m² / 109 m² 仅描边 |
| App 内俯视、带语义地面格、149 帧真机 | **未** |

第 3 节产品门：**「壳不填」在无语义样本上视觉成立；「路径青/黄格 + 短紫墙 + 真机」仍待同图闭环。**
