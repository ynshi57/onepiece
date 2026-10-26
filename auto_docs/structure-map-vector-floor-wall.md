# StructureMap 墙/地面矢量 — Codex 实现交接

## 用户裁定（2026-10-23）

- floorTiles / LocalFloor / VisibleFloor 格体系已删除；非房间建图必需资源不再投入。
- 细节由 Cursor 写入协作面，**Codex 负责实现与验证效果**。

## 共识方案要点

| 项 | 决定 |
|---|---|
| 真源 | `SpatialStructureMap` 同一 `revision` 供俯视、3D、保存回读 |
| 墙面 | 现有 `SpatialStructureP0Builder` + track/corner；候选跨度 ≠ 卷尺验收墙长 |
| 地面 | 新增 `floorRegions`（闭合环），**不是**格并集、**不是** ARKit 壳填 |
| 状态 | 无独立尺寸 / 环不闭合 → `incomplete`，禁止静默 fill 冒充完成 |
| Cursor 不抢改 | `SpatialStructurePairPolicy` / `SpatialPlaneCornerSolver` 及对应单测 |

## Codex 交付清单

1. **类型**：`SpatialStructureMap.swift` — `FloorRegion`（环、洞、provenance、与 floor track 关联）。
2. **构建**：`SpatialStructureP0Builder` 或独立 `SpatialStructureFloorRegionBuilder` — 墙段端点 + verified floor 平面 → 环；失败写 `quality`/`gaps`。
3. **持久化与缓存**：scan store 存 structure revision；位姿/epoch 变失效。
4. **UI**：`SpatialScanScreen` / `SpatialTopSceneCache` — 结构层墙 + 地面轮廓；hypothesis 不抢尺寸叙事。
5. **验证**：
   - 单测新增/更新
   - 白墙 `9B1AF814-B8C4-4C84-8CE3-1C38AEBEF806`，12–20 帧 `-spatial-structure-p0-verify`
   - 贴：`structureP0` PNG、JSON receipt、`floorRegions` 点数/面积、打开俯视 wall time
6. **文档**：仅**看图过门后**改 `docs/SPATIAL_HANDOFF.md` 一行；过程写 `docs/evolution/2026-10-23-structure-floor-regions.md`（若需要）。

## 已验证事实（Cursor）

- 删除格体系后：`SpatialSceneHypothesisTests` 等 — TEST SUCCEEDED（2026-10-23 Simulator）。
- 白墙墙脚 audit：无跨视支持角点；P0 实验 incomplete — **不能**因删格宣称建图完成。

## 风险

- 仅平面交点仍可能缺角；floor 环 v0 可能只有局部 L 形 — 须 UI 标 incomplete + 补扫指引。
- 勿用 Mac 缓存 A/B 当端上性能证据。

## 待办（Codex）

- [x] 实现 floorRegions 连接图 builder + 消费链（代码在库；效果未过门）
- [x] 白墙真实metadata PNG + receipt（0角点/0墙/0环，incomplete；非照片实验）
- [x] P0 合成房断言 `floorRegions.isEmpty`；`quality` 区分无角点 vs 开链
- [x] 讨论文件 EXECUTION 回填（含阻塞与未验收项）
- [ ] 过门后更新 SPATIAL_HANDOFF（禁止把 0 环写成建图完成）

## 2026-10-23 15:05 共识

Codex「不为截图强行连边」**采纳**。闭环/自交/洞/保存 revision 的单测多数已在 `SpatialStructureFloorRegionBuilderTests` 与 `SpatialStructureSnapshotTests`。白墙组不成环是 P0 只出两段墙的几何事实，下一步是墙段覆盖，不是补凸包。

更新时间: 2026-10-23 15:05

最终回执见讨论文件 Codex EXECUTION；真实存档缺扫描、产品实屏及P1墙段覆盖未验收。不得提升为建图完成。

## 2026-10-23 15:35 看图结论（未执行下一假设）

白墙 PNG 诚实：墙段 0、地面闭环 0。失败点是 **ARKit 有限面片够不到三平面交点**，不是环算法。  
**保留**地面环契约。**停止**「角点盖住才出墙」。  
**候选下一刀（待用户确认）：** 墙地交线裁到有限面跨度后直接发布墙段；角点降为两条交线相遇；仍 incomplete 直到成环。

更新时间: 2026-10-23 15:35

## 2026-10-23 15:40 用户确认执行

用户「是」。Codex 拆角点总闸门，先发墙地交线墙段。裁切 = 墙顶点在交线上的横向跨度（与现 `wallSegments` 相同），**不要**用「顶点到交线 3D≤8cm」过滤。语义必须 `.wall`。环契约不变。白墙过门：橙色墙段可见，0 环仍 incomplete。

更新时间: 2026-10-23 15:40

## 2026-10-23 墙地交线验收

用户确认后已实现：严格.wall × verified floor先发布有限候选线，角点不再总闸门；地面环契约不变。白墙metadata真机回放：1段0.450m、0角点、0环、incomplete；24项相关测试及最后4项边界/缓存测试通过，device构建通过。最终PNG与JSON位于`/tmp/vqasee-wall-floor-lines-final-{top.png,check.json}`，结构0.106s；完整回执见讨论文件。仅局部候选门通过，存档/实屏/尺量仍未验证，禁止写房间完成。

## 2026-10-23 15:55 Cursor 看图

本轮墙段门 **过**（橙线可见）。房间 **不过**（1×0.45 m stub，未贴长灰边）。16/17 竖直 track 非严格 `.wall` — 禁止放宽。下一步若继续：审计那些长面片的 semantic 计数。

更新时间: 2026-10-23 15:55

## 2026-10-23 16:05 语义审计

17 条竖直面：1 条已是墙（0.45 m）；4 条墙/其他帧打架；11 条不足两帧决断；1 条稳定 other。两条 ~2.2 m 够高长边都是打架，不是交线没算。不改 0.7 / 两帧门。

更新时间: 2026-10-23 16:05

## 2026-10-23 Cursor 实现：沿交线局部墙掩码

用户改由 Cursor 实现。整面 `.wall` 仍发几何跨度；整面 `.other` / 矮面拒绝；unknown 混杂高面只发通过 0.7 / 两帧 / 8 cm 箱的局部区间。算法版本 `structure-wall-floor-lines-v3`。

**已看图** `.cursor-tmp-wall-line-v3-top.png`（同 `/tmp/vqasee-wall-line-v3-top.png`）：橙线 5 段，最长贴上顶部灰边 1.28 m；右侧原 0.45 m stub 仍在；另有 0.16 m 与两段 8 cm。无假闭环、无黄格。房间 **未完成**。

Swift 对同一 `/tmp/vqasee-white-room-document.json`：`testWhiteRoomMetadataPublishesLocalWallIntervalsWithoutClosingRoom` passed（0.974 s），最长段 source=`34AD2185`、state=incomplete、0 环。相关 P0/WallLineSupport/Snapshot/FloorRegion 单测先前 TEST SUCCEEDED。

未验收：产品实屏、真机存档、尺量、照片。禁止写房间完成。

## 2026-10-25 实验栈清理

已删整图深度 / DA 室内相对深度 / MetricDepth A/B / P0 crease / 照片墙脚 / FramePool / PublishedMapOutline 及其单测。旧扫描 `depthObservations` / `relativeDepthObservations` 仅归档解码。现行路径保留：采集、室内语义、P0 墙/地、M2 可见面、物品记录、户外 DA。

聚焦单测 52 passed（Scan / P0 / SceneHypothesis / Location / store round-trip）。未宣称房间完成或看图过门。未提交。

更新时间: 2026-10-25 20:52

## 2026-10-26 RGB 语义立体（无 LiDAR）

用户否决 sceneDepth：iPhone 17 无 LiDAR。下一刀改为墙地轮廓像素 + 已有 `triangulate` 门，不打开 DA affine。占用轮廓“已有墙”判定改为 16cm（与 DP 简化同尺度），避免 8cm 网格外偏画出双线。平面 track 分桶按 key 排序，避免 Dictionary 无序导致同扫描 track id 抖动。

14B7 回放（138 张 RGB、0 深度）：stereo 点数不足 8，占用因 16cm 去重未再发与 0.80m 墙平行的 14cm 外偏线。第四面墙仍缺。合成两帧无 LiDAR 三角化通过。未提交。

更新时间: 2026-10-26 00:36
