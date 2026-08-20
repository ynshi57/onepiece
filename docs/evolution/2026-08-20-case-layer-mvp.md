# 演进记录：闭环 case 层 MVP（DCL 降维落地第一步）

- 日期：2026-08-20
- 触发：用户认同「iPhone 端录制数据的本意与 DCL 车端录制一致」，同意先落地 case 层 MVP。
- 参考：`docs/tech-radar/2026-08-20-dcl-data-closed-loop-architecture.md`（DCL 架构沉淀）。

## 乔布斯定方向

VQASee 之前的闭环缺一环：eval 能算出「190 帧漏报」，但没有任何**持久载体**回答
「这个问题被跟踪到关闭了吗？下次又出现了吗？」。散在 model-lab / evolution 的散文不可查询、
不可重开。借 DCL 的「统一载体 + 生命周期状态机 + AutoTriage」降维，做一个**本地、轻量、
commit 安全**的 case 层，让「一个问题发生两次就是系统没学会」这条 AGENTS 原则第一次有数据支撑。

## 员工 review

- 罗根（系统）：坚持不引入 Kafka/DB，用「每 case 一个 JSON + 确定性 id」即可等价降维；
  确定性 `case_id={dataset}:{failure_type}` = 幂等 upsert，重跑更新不重复。通过。
- 全麦（模型/评测）：聚类必须复用逐帧页同一套失败判定，否则 UI 和 case 会各说各话。
  已把 risk_miss/false_block 判定收敛到 `case_store.frame_failure_types`，逐帧页 `_frame_flags`
  改为复用它。通过。
- 思余（UI）：case 列表要「未收敛的浮在上面」，详情要能一眼看到状态、根因、关联修复、历史。
  已实现。通过。

## 做了什么

1. `server-vqa/app/case_store.py`（新）：
   - 规范失败判定 `frame_failure_types`（区域级 risk_miss/false_block），成为 UI 过滤与 case 聚类的**唯一真源**；
   - `cluster_failures`：把一次 eval 的失败帧按类型聚成带确定性 id 的簇；
   - 生命周期状态机 `new→triaged→fixing→verified→released / reopened / closed`；
   - **回归式自动重开**：已验证 case 只有当失败帧数**超过验收时的 `resolved_frame_count`** 才 `reopened`，
     稳定残留不会反复打扰（这是把「发生两次自动重开」做对的关键取舍）；
   - `upsert_clusters / set_status / annotate`，每步落 history，非法状态/未知 case 显式抛错。
2. `server-vqa/app/diagnostic_api.py`：
   - `_frame_flags` 改为复用 `frame_failure_types`，杜绝 UI↔case 漂移；
   - 新增 `/cases/cluster`（POST 聚类）、`/cases`、`/cases/status`、`/cases/annotate`、
     `/cases/ui`（列表）、`/cases/detail/ui`（详情，含状态推进/笔记/历史）；
   - iPhone 真身评估页加「把失败帧聚成 case」一键入口；诊断首页加 case 模块。
3. 回填**两条真实 case**（非编造）：
   - `camvid-manifest:risk_miss`：采样 bug 漏报簇 **190→2，已验证**；frame_ids 为修复后真实残留 2 帧，
     190 来源 model-lab 采样bug文档，关联修复指向该文档与 `LocalSegmentation.swift`；
   - `camvid-manifest:false_block`：修复后暴露的**误阻挡 566 帧（帧级），已分诊**，根因＝ROI 阈值仍在旧概率尺度，
     关联 `sweep_seg_threshold.py`，对应 backlog 的 recalibrate-roi。
4. 单测 `server-vqa/tests/test_case_store.py`（10 例）：分类正确性、与逐帧页判定一致、
   确定性 id、幂等 upsert、**稳定残留不重开 / 真回归才重开**、非法状态与未知 case 显式失败。

## 验证

- `pytest server-vqa/tests`：**206 passed**（含新增 10 例，且 `_frame_flags` 重构未回归）。
- TestClient 冒烟：`/cases/ui`、`/cases/detail/ui`、`/cases`、`/cases/status`、`/cases/cluster` 均 200。
- 真实数据：对 `docs/datasets/camvid-manifest.jsonl` + `/tmp/camvid-manifest-ios-harness.jsonl`
  重聚类，已验证的旗舰 case **不会误重开**（残留 2 ≤ 验收 2）。

## 诚实边界（未做/留待）

- 只聚类**区域级** risk_miss/false_block；引导线级 missed_path/false_go 仍是聚合量，未做帧级 case（tech-radar 已记后续）。
- case 只存短 frame_id 与标量，**不存图片/绝对路径**，故 case 详情暂不深链到逐帧图（保 commit 安全）。
- 聚类粒度是「数据集×失败类型」，未再按场景/区域细分子簇；真实需要更细时再加。
- 未接入设备端「事件触发录制 / 上传协议 / 断点续传」——那是 P3 的下一步，本轮只做平台侧 case 载体。

## 沉淀

- 代码/测试：`case_store.py`、`diagnostic_api.py`、`test_case_store.py`。
- 真实样例：`server-vqa/cases/*.json`（第一批 case，commit 安全）。
- 本记录 + roadmap P3 标注 case 层 MVP 已落地；tech-radar 卡片「沉淀与后续」更新。
