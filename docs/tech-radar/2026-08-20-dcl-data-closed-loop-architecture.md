# 技术雷达：DCL 数据闭环平台架构（自动驾驶）→ VQASee 闭环平台产品化参考

- 日期：2026-08-20
- 小马结论：L1 学习更新（架构参考沉淀）；其中「事件触发录制 / 前后端协议 / 断点续传」列为 L3 产品候选，进 roadmap
- 相关角色：乔布斯 / 罗根 / 思余 / 全麦

> 本文沉淀一份外部**自动驾驶 DCL（Data Closed Loop，数据闭环）**系统架构文档，并把它的架构
> 纪律降维映射到 VQASee 闭环实验平台。目的：VQASee 现在还是 demo 阶段、暂不需要这么重的平台，
> 但**未来一定会需要**；先把「对的架构骨架」记下来，避免 demo 长成一次性脚本堆。

## 1. 来源与可信度

| 来源 | 类型 | 可信度 | 备注 |
| --- | --- | --- | --- |
| 《DCL 数据闭环 · 系统架构与核心能力》飞书 wiki | 内部生产系统架构文档 | 高 | 车队规模、已上线的真实闭环；描述规则下发 / DCL 解析 / 事件管理（ClipHub）/ AutoTriage / Issue 全链路。 |
| 《自动驾驶数据闭环平台（DCL）产品说明手册 V1.0》（2026-08-20） | 内部产品文档 | 高 | 组件级实现 + 技术栈（EMQX/Kafka/Mongo/ES/Argo）+ 真实规模（52 万 clip）。细节沉淀见 §11。 |
| VQASee 闭环实验平台现状 | 内部代码事实 | 高 | 本仓 `server-vqa` + `ios-vqa-app` + harness + baselines，本文映射基于代码现状。 |

## 2. 核心认知（抽掉分布式外壳后的架构骨架）

DCL 的价值不在 Kafka/MQTT/Argo，而在**六条可复用的架构纪律**：

1. **统一载体 + 确定性主键**：一切以 `clip` 为载体，`clip_id={VIN}_{ts}_00000` 确定性生成，
   贯穿「事件—数据—分诊—工单」。根治「事件在 MQTT、数据在流水线、工单在飞书、素材在 BOS，
   彼此没有统一主键与生命周期」的分裂。**确定性 ID = 幂等 + 可去重 + 可续传的前提。**
2. **显式生命周期状态机**：`data_status: pending → submitted → … → completed`；
   规则版本 `draft → static_validated → approved → released → deprecated`。每一步可观测、可追溯。
3. **事件触发录制**：云端规则决定「**什么条件触发、触发后录哪些传感器 topic 及前后时间窗**」，
   车端命中规则才录制、并做多事件聚合降冗余。不是一直全量录，而是**按价值触发**。
4. **系统间通信矩阵**：每条边都有明确的「起点→终点 / 方式 / 通道·接口 / 说明」。
   通信协议是**一等公民**，不是隐式约定。
5. **可追溯快照 + 对账**：每步落 `dcl/*.json` 快照，供离线追溯对账。
6. **清晰边界与状态标注**：一张系统清单写明「归属（我方/外部）」+「已上线 / 规划中」。

## 3. 对 VQASee 的机会

- 解决哪个瓶颈：**产品闭环**（从「发现问题」走到「每个问题被跟踪到关闭」）+ 未来的**真机现场数据回流**。
- 可能收益：把 demo 阶段的实验平台，升级为可承接**真机现场事件 → 回流 → 评测 → 修复 → 灰度 → 验证**的产品级闭环。
- 适用场景：iPhone 现场遇到风险/漏报/不确定 → 触发录制一小段 clip → 回传平台 → 进评测集 → 驱动下一轮模型/配置迭代。**这正是用户认同的「iPhone 端录制数据的本意」与 DCL 车端录制同构。**

## 4. VQASee 现状 ↔ DCL 概念映射（诚实对照）

| DCL 概念 | VQASee 已有 | 差距（本文要补的设计） |
| --- | --- | --- |
| clip 统一载体 + 确定性 ID | manifest 的 `frame_id`（逐帧） | 缺**跨反馈→修复→验证的持久 case/clip 对象** |
| 车端事件触发录制 | 无（现在是离线数据集导入） | 缺**设备端触发规则 + 环形缓冲 + clip 打包上传** |
| 车端→BOS 原始上传 | iOS WebSocket `diagnostic_frame` 单帧上传 | 缺**clip 级、大文件、断点续传**上传 |
| 解析流水线产素材 | **真身 harness**（设备同款离线跑，产 `guidance_path`/预测） | 已具备（今天靠它钓出采样 bug） |
| data_status 状态机 | harness 缓存的内容指纹（新鲜度） | 缺**clip/case 级生命周期状态** |
| 规则/配置下发 + 版本 + 灰度 | `PerceptionConfig` + OTA 端点 + `config_version`/`content_hash` | 缺**版本状态机 + 审批 + 灰度/回滚** |
| AutoTriage 自动分诊 | 逐帧页 `risk_miss/false_block/mismatch` 人工筛选 | 缺**自动聚类成问题簇 + 优先级 + 疑似根因** |
| 沉淀工单驱动迭代 | `docs/model-lab / evolution / decisions` 散文 | 缺**可跟踪、可回归验证的 case 对象** |
| 通信矩阵（协议一等公民） | WebSocket 消息较隐式 | 缺**版本化协议 schema + ack/回写 + 错误语义** |
| 可追溯快照 + 门禁 | eval 基线 + `gate_guidance`/`check_regression` + meta sidecar | 较完整（今天基线前移即靠它） |

## 5. 前瞻设计（面向 demo → 产品化，三块用户点名的能力）

> 原则（罗根）：**借架构思想，不借基础设施**。Kafka/MQTT/Argo/ES 是车队规模的必需品；VQASee 是
> 「一台 Mac + 一部 iPhone」，用 WebSocket/HTTP + 本地存储（SQLite/JSONL）+ 状态机即可等价降维。

### 5.1 事件触发录制（对标 DCL 规则下发 + 车端录制）

- **触发源（设备端）**：不再一直全量推帧，而是命中「值得录」的条件才打包 clip：
  - 引导线 `status=insufficient`（看不出路）；
  - 分割 vs YOLO 分歧大（两套感知打架）；
  - 低置信 / 高不确定；
  - 用户手动「这里不对」按钮（人工接管信号，最高优先级）。
- **触发规则可下发**：复用 `PerceptionConfig` 作为「录制规则」载体（对标 DCL 的 Lua+meta），
  字段示例：`record.triggers[]`（条件）、`record.pre_ms/post_ms`（前后时间窗）、`record.topics`
  （录 RGB / 深度 / 感知输出元数据）。
- **降冗余**：设备端环形缓冲最近 N 帧；同一 clip 内多触发聚合成一条，避免风暴。
- **产物**：一个 clip = 短片段帧 + 每帧感知元数据（`guidance_path`/ROI/检测框）+ `config_version`。

### 5.2 前后端通信协议（对标 DCL 通信矩阵，把协议当一等公民）

- **统一信封**：所有上行消息带 `{ protocol_version, clip_id, seq, config_version, kind, payload }`。
  `clip_id` 设备端**确定性生成** `{device_id}_{clip_start_ts}`（= 幂等 + 去重 + 续传主键）。
- **消息族（版本化 schema，进 `docs/decisions` 定契约）**：
  - `clip.open`（声明一个 clip，带触发原因、预计分片数、总大小、内容哈希）；
  - `clip.chunk`（分片数据，带 `chunk_index / offset / bytes / chunk_sha256`）；
  - `clip.close`（收尾，带整体 `sha256` 供服务端校验）；
  - `clip.ack`（**服务端回写**：已收到哪些 chunk、data_status、下一步该传什么）。
- **回写而非静默**（遵守 AGENTS「不允许静默失败」）：每个上行都有 ack；失败带明确错误码
  （schema 不符 / 哈希不符 / 版本不兼容），设备端可见并可恢复。
- **兼容性**：`protocol_version` 不匹配时服务端明确拒绝并提示升级，绝不静默丢弃安全相关数据。

### 5.3 断点续传（对标 DCL 原始 tar 上传，移动网络必需）

- **分片 + 幂等**：clip 切成固定大小 chunk；`clip_id` 确定性 → 重连后 `clip.open` 幂等，
  服务端返回**已持久化的 chunk bitmap**，设备端只补缺失片，不从头重传。
- **完整性**：每片 `chunk_sha256` + 整体 `sha256` 双重校验；`clip.close` 校验失败 → 明确报错、
  标记该 clip `corrupt` 而非当成功。
- **状态机**：clip `data_status: uploading → complete → parsed → triaged`，与 DCL 的
  `pending→…→completed` 同构；断连只是停在 `uploading`，恢复即续。
- **落地形态**：服务端本地目录 `data/clips/{clip_id}/`（对标 BOS 目录）+ 一条 clip 记录
  （SQLite/JSONL），**不上对象存储/消息队列**。

### 5.4 案例（Case）层（对标 clip 载体 + AutoTriage + Issue，最大缺口）

- eval 跑完，把 `risk_miss/false_block/missed_path` 帧**自动聚类**成 case（按区域/类型/场景/疑似根因），
  每个 case 带确定性 id、状态机（`新建→已分诊→修复中→已验证→已发布/已回滚`）、
  首次暴露的 baseline、关联修复（commit / model-lab 文档）。
- 价值：AGENTS 原则「一个问题发生两次就是系统没学会」终于有数据支撑——**case 复现 = 自动重开**。
  今天「采样 bug → 190 漏报簇 → 已修复 → 新基线验证」应作为**第一条真实 case 回填**。

## 6. 风险与不确定性

- 技术风险：设备端环形缓冲 + 触发录制会增加内存/功耗；需按帧预算测（罗根）。
- 产品风险：录制/回传涉及**隐私**（图像），必须默认最小持久化 + 明确同意/提示（AGENTS 隐私原则、思余文案）。
- 系统/性能风险：断点续传的分片/哈希在移动弱网下的重试与超时策略要实测。
- 数据/评测风险：现场回流样本会引入分布漂移；进评测集前要去重、脱敏、标注来源（全麦）。

## 7. 分角色学习卡

### 乔布斯
- 产品影响：闭环从「离线数据集实验」延伸到「真机现场事件回流」，是产品级闭环的关键一跳。
- 路线图/闭环影响：demo 阶段先不做全量，但**协议与 clip_id 的确定性主键要现在就定对**，否则将来重构代价大。
- 下一次要多问：这条回流真能让「发布/回滚」判断更可信吗？隐私同意是否闭环？

### 罗根
- 系统/性能影响：坚持 WebSocket/HTTP + 本地存储 + 状态机，拒绝 Kafka/Argo/ES；续传/幂等靠确定性 `clip_id`。
- 需要观测的指标：上传成功率、断点续传恢复率、clip 端到端时延、chunk 重传率、设备端录制内存/功耗。
- 下一次要多问：弱网/切网/后台被杀时，clip 是否能可靠恢复而不丢安全事件？

### 思余
- UI/体验影响：设备端「这里不对」手动触发按钮 + 录制/上传状态可见；隐私提示自然、非技术化。
- 用户理解风险：用户要清楚「录了什么、传到哪、能不能关」，不能让人觉得被偷偷录。
- 下一次要多问：录制/上传中的状态（进行中/暂停/失败/已完成）是否一眼可懂？

### 全麦
- 模型/评测/推理影响：现场触发的 hard case 是最有价值的评测样例来源；AutoTriage 自动聚类失败簇 + 疑似根因喂下一轮。
- 需要的样例和指标：risk_miss/false_block/missed_path 聚类；现场 clip 的去重、脱敏、来源标注。
- 下一次要多问：这个 case 是否能稳定复现、进回归？它暴露的是模型、配置阈值，还是接入 bug？

## 8. 最小实验（不在本轮实现，作为 L3 候选）

- 假设：若给闭环加「确定性 clip_id + 版本化上传协议 + 断点续传 + case 层」，现场 hard case 能可靠回流并驱动迭代。
- 改动范围（未来）：iOS 录制/上传、`server-vqa` 上传端点 + clip 存储 + case 表、协议契约文档。
- Baseline：现状为离线数据集导入 + 单帧 WebSocket，无 clip/case/续传。
- 验证：弱网/切网下 clip 续传恢复率 = 100%、哈希校验 0 误收；case 复现自动重开。
- 失败退出：若移动端功耗/隐私不可接受，退回「仅用户手动触发 + 小片段」最小形态。

## 9. 沉淀与后续

- 是否更新 AGENTS.md：暂否（本文为架构参考沉淀；待 case 层/协议进入实现时再固化规则）。
- 是否更新 skill：暂否；`vqasee-self-evolution` 已覆盖闭环平台演进调度。
- 是否进入 docs/decisions：**待实现前**，把 §5.2 上传协议 schema 单独立一份 decision 定契约。
- 是否进入 docs/model-lab / performance / ui-lab：现场 clip 评测报告未来进 model-lab；录制功耗进 performance。
- 是否进入 roadmap：**是**，新增「闭环平台产品化（事件触发录制 / 上传协议 / 断点续传 / case 层）」。
- 下一次主动雷达主题：移动端**弱网可靠上传**与**端侧事件触发**的开源实践（如分片续传库、端侧 ring buffer 录制）。

## 10. 落地进展

- 2026-08-20：**§5.4 case 层 MVP 已落地**。见 `docs/evolution/2026-08-20-case-layer-mvp.md`。
  - 平台侧实现 `case_store.py`：确定性 `case_id={dataset}:{failure_type}`、生命周期状态机、
    回归式自动重开（残留稳定不打扰、真回归才重开）；诊断平台加 case 列表/详情 UI + 一键聚类入口。
  - 回填两条真实 case：采样 bug 漏报簇 190→2（已验证）、误阻挡 566（已分诊，待重标定阈值）。
  - `pytest server-vqa/tests` 206 passed。
- 仍未落地（下一步 P3）：设备端**事件触发录制**、**版本化上传协议**、**断点续传**——需 iOS 改动，
  实现前先在 `docs/decisions` 立 §5.2 协议契约。

## 11. 产品手册补充（DCL 产品说明手册 V1.0，2026-08-20）

> 来源新增：《自动驾驶数据闭环平台（DCL）产品说明手册 V1.0》（内部产品文档，可信度高）。
> 相比 §1 的架构文档，这份手册给出了**组件级实现、技术栈和真实规模数字**。本节只记录
> 「架构文档没有、且对 VQASee 有新启示」的部分，避免与前文重复。

### 11.1 手册里的硬事实（参考）

- **ClipHub 统一数据模型**：一个 clip 文档聚合「3 类文件（topic/raw/record）+ 7 类子文档
  （vehicle/recording/event/ingest/check/labels/tags）」；事件本质是 `clip_type=event` 的特殊 clip，
  **与常规数采数据同库同模型**。状态机 `pending → … → completed` 由 `dcl_consumer` 消费解析完成消息后推进。
- **技术栈**：EMQX（MQTT Broker）→ bridge 转发到 Kafka(`dcl_event`) → Python consumer 确定性生成
  `clip_id={VIN}_{trigger_ts}_00000` → 写入 MongoDB `clips` 集合；检索基于 Elasticsearch 演进的布尔接口
  `/clips/search`（全量计数约 1.6s）。解析走 Argo/DP 工作流（vehicle-data-parser）。
- **规模**：约 **52 万条 event clip** 秒级筛选；2026-08 单批 **544 条**场景补漏入库、S6/S7/S8 扩容 **274 条**。
- **配置单一真源**：`fieldspec`（字段定义中心）+ `opthub`（选项集单一真源）+ `autorule`（自动化规则登记）。
- **AutoTriage**：`triage-trigger` 触发，分诊结果**自动回写** `clip.autotriage.*`，无需人工录入分类。
- **工单双向同步**：飞书工作项 ID ↔ `ingest.issue_id`，DCL Issue ID ↔ `ingest.dcl_issue_ids`。
- **OpenAPI 跨系统**：稳定性平台 FailSafe 用 (VIN, 时间, 故障码) 检索，返回 clip_id/webviz_url/Triage 结论。
- **运营/测试双 Tab**：按车辆用途自动分流，两 Tab 数据**完全隔离**；均支持常规/高级检索 + 可存「个人/公开视图」。

### 11.2 相比架构文档，对 VQASee 的**新增**启示

1. **MQTT「保留消息」→ VQASee 配置/状态的「最后已知值」语义**。手册强调：新订阅者订阅即可拿到主题最新
   保留消息，即使订阅发生在发布之后。映射：VQASee 的 `PerceptionConfig` OTA、设备连接状态，都应有
   **retained「最后已知配置/状态」**语义——设备重连后立即拿到当前应生效的配置，而不是等下一次推送。
   这补强了 §5.2 协议：`clip.ack` 之外，应有一个「当前配置快照」的 retained 端点（VQASee 已有
   `/runtime/perception-config`，符合方向，但要明确它是「最后已知真源」）。
2. **fieldspec/opthub 单一真源 → VQASee 的「失败类型/标签/选项」也要单一真源**。这正呼应本轮
   case 层把 `risk_miss/false_block` 判定收敛到 `case_store.frame_failure_types` 的做法：**分类口径必须
   有唯一定义处**，否则 UI、聚类、门禁会漂移。下一步 VQASee 的 case 失败类型、场景标签应集中登记，
   而不是散在各处字符串。
3. **AutoTriage 自动回写 `clip.autotriage.*` → VQASee case 的下一步**。目前 VQASee 的 `cluster_failures`
   只做「按失败类型聚类」；对标 AutoTriage，应把**疑似根因、优先级、场景子簇**也自动算出并回写进 case
   （而非只靠人工填 `suspected_cause`）。这是 case 层从「MVP」走向「AutoTriage-lite」的明确增量。
4. **运营/测试双 Tab 数据隔离 → VQASee 现场帧 vs 数据集帧必须分流**。未来真机现场回流的 clip 与
   离线开源数据集（CamVid 等）**不能混在一个池子评估**，否则分布漂移会污染指标。case 与 manifest 都应带
   **来源标签（现场/数据集、运营/测试）**，评估和 case 列表可按来源隔离查看。
5. **OpenAPI 跨系统权威源 → VQASee case/eval 结果应可被只读检索**。手册里 ClipHub 是「打标/分类/筛选的
   唯一权威源」，并对外开放 OpenAPI。VQASee 对应物：case + baseline 应是**质量真源**，未来给 App 内
   「反馈中心」或团队面板一个只读检索入口（现在 `/diagnostics/cases` 已是雏形）。
6. **规模数字校准「我们现在不需要什么」**。52 万 clip、ES 检索、Kafka、Argo 工作流、Mongo 分库——这些是
   **车队级**必需品。VQASee 当前是「一台 Mac + 一部 iPhone + 几百帧 CamVid」，**继续用本地 JSON + 状态机**
   即可；只有当现场 clip 量级到「万级/需要并发检索」时，才谈得上引入索引/队列。**过早引入＝给 demo 上镣铐。**

### 11.3 结论（小马）

- 这份手册**不改变** §5 的降维设计方向，反而验证了它：确定性 id、生命周期状态机、单一真源、AutoTriage、
  跨系统只读开放，都是 VQASee 该借的**纪律**；EMQX/Kafka/Mongo/ES/Argo 是该**丢**的重量。
- 三条可立即纳入 backlog 的增量：`retained 配置真源语义`、`失败类型/标签单一真源登记`、`case AutoTriage 回写疑似根因`。
- 一条要守住的红线：**现场帧与数据集帧分流**，别让回流数据污染离线基线。
