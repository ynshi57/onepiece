# 时序融合 A+B+C 落地纪要

## 结论

按 `docs/performance/2026-09-28-temporal-perception-fusion-design.md` 完成 **A+B+C** 的最小安全实现；D–E 仍未做。

## 已有 / 本轮补齐

- **A mailbox**：`FrameCaptureProxy` / AR 单槽 latest-only、`LocalPerceptionDelivery`（age + replaced）；复位使用会话代际令牌，旧 Core ML 任务不能向新会话回写。
- **B 对象融合**：`TemporalPerceptionFusion.swift` — `TemporalFusionConfig`、`StaleResultPolicy`、`RiskLatch`、`LocalObjectTemporalFusion`（2 帧确认 / 高风险首帧绕过 / 250ms 预测 TTL）。过期优先风险仅发布风险对象，绝不带旧道路几何；本地风险等级与慢速远程结果取较高值。
- **C 车道/路径融合**：仅对新鲜、同语义且位移小的相邻 `LanePolyline` / `GuidancePath` 做 60% 最新观测插值；漏检、跳变或过期时不保留旧路径。
- **单测**：stale 策略、风险闸门、确认门、预测过期、晚到风险剥离道路几何、车道/路径微抖平滑与跳变直出。

## 未做（设计 D–E 与验收）

可走区重投影、OOD 状态机、harness 视频序列、真机 p95 与逐帧 overlay 签字。

更新时间: 2026-09-29
