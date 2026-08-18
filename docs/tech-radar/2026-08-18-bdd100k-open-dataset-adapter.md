# 技术雷达：BDD100K open dataset adapter MVP

- 日期：2026-08-18
- 小马结论：L2 最小实验
- 相关角色：乔布斯 / 罗根 / 全麦 / 思余

## 1. 来源与可信度

| 来源 | 类型 | 可信度 | 备注 |
| --- | --- | --- | --- |
| BDD100K drivable area 本地标签格式 | 开源数据集 / 本地已下载数据 | 中 | 本次先按常见 `labels[].category = drivable area` + `poly2d` polygon 适配；不在平台内下载数据或绕过 license。 |
| VQASee open dataset registry | 内部模型实验规划 | 高 | 已在 `docs/model-lab/2026-08-13-open-dataset-registry.md` 中把 BDD100K 列为 P1 road-risk 数据源。 |

## 2. 核心认知

- 开源数据集接入不能只写“把 manifest 放进 docs/datasets”；平台需要有明确的 dataset adapter 入口。
- MVP 不应内置下载器；大数据集通常有 license、账号、体积限制，应读取用户本地已下载数据。
- BDD100K drivable area 可以先转成可通行 mask，再复用 VQASee 现有 near/left/right ROI 评估逻辑。
- 该 adapter 只解决道路/驾驶风险方向，不代表室内、户外人行道或读文字评估已经接通。

## 3. 对 VQASee 的机会

- 解决哪个瓶颈：产品闭环 / 反应快前的离线评估 / 道路风险数据覆盖。
- 可能收益：闭环平台从“本地图片+mask导入器”升级为“开源数据集接入入口”。
- 适用场景：道路、车辆、车道、可行驶区域相关 path-guidance ground truth。

## 4. 风险与不确定性

- 技术风险：BDD100K 不同导出版本的 JSON 字段可能有差异，后续需要更多真实样例验证。
- 产品风险：用户可能误以为平台会自动下载数据；UI 已明确“本地已下载”。
- 系统/性能风险：大标签文件和大量图片可能导致同步生成较慢，后续需要后台任务和进度条。
- 数据/评测风险：drivable area 是车辆视角，不等价于手持 walking 场景。

## 5. 分角色学习卡

### 乔布斯

- 产品影响：用户之前提出的开源数据集接入已进入平台主流程。
- 路线图/闭环影响：下一步要明确 ADE20K / Mapillary / ScanNet 的优先级，而不是继续停留在 registry。
- 下一次要多问的问题：这个数据集覆盖哪个真实场景？是否真的让发布判断更可信？

### 罗根

- 系统/性能影响：当前是同步本地转换；大规模数据需要异步任务、进度、取消和错误报告。
- 需要观测的指标：转换帧数、跳过图片数、耗时、失败记录、输出 manifest 大小。
- 下一次要多问的问题：adapter 对异常目录和字段是否可恢复？

### 思余

- UI/体验影响：入口已分成“接入开源数据集”和“图片+mask目录”。
- 用户理解风险：必须持续强调“不自动下载、不处理 license”。
- 下一次要多问的问题：用户能否知道自己该填哪个本地路径？

### 全麦

- 模型/评测/推理影响：BDD100K 可用于 road-risk path guidance ground truth，但还缺 prediction generation。
- 需要的样例和指标：risk_miss、false_block、unknown rate、focus_direction accuracy。
- 下一次要多问的问题：这个 ground truth 是否能暴露模型漏报，而不是只评估本地几何规则？

## 6. 最小实验

- 假设：如果接入 BDD100K drivable area，本地开源道路数据可以自动生成 VQASee path-guidance manifest。
- 改动范围：`open_dataset_adapters.py`、diagnostics UI、API 测试。
- Baseline：此前只能手动把数据转成 manifest 或使用图片+mask通用导入器。
- 验证命令：`source .venv/bin/activate && pytest server-vqa/tests/test_dataset_manifest_tools.py server-vqa/tests/test_api.py -q`
- 成功标准：能从本地 BDD100K-style JSON + 图片生成 manifest，并能进入现有评估流程。
- 失败退出：若真实 BDD100K 字段不兼容，扩展 parser 并加入 fixture；不改变 evaluator 适配假数据。

## 7. 沉淀与后续

- 是否更新 AGENTS.md：否，本次是代码事实和技术雷达沉淀。
- 是否更新 skill：否，小马机制已覆盖 open dataset adapter 学习。
- 是否进入 docs/decisions：暂不进入，等选择长期数据集路线后再写。
- 是否进入 docs/model-lab / performance / ui-lab：本条已在 tech-radar；后续真实数据评测报告进入 model-lab。
- 是否进入 roadmap：建议加入“Open dataset adapter pack”。
- 下一次主动雷达主题：ADE20K semantic segmentation → VQASee indoor/outdoor path manifest adapter。

## 2026-08-18 UI 修正补充

用户反馈：接入页要求填写本地已下载 BDD100K 路径，但用户还没下载数据，不知道从哪里下载、下载哪个文件、路径怎么填。

修正：

- 增加内置 BDD100K-style demo，用户不下载真实数据也能跑通 manifest 生成和浏览。
- 页面增加官方下载入口提示、license/账号说明、推荐目录结构和字段解释。
- 将表单顺序改为：先 demo → 再下载 → 最后填路径。

小马结论：开源数据集 adapter 的产品入口必须包含“数据准备认知”，否则 adapter 虽然存在，用户仍然无法使用。

## 2026-08-18 再修正：CamVid 成为默认开源入口

用户反馈 BDD100K 需要账号/license/大文件，不能作为默认入口。小马重新判断：第一个开源数据集入口必须低门槛、可直接下载、免账号。

执行结果：

- CamVid GitHub 镜像成为默认入口；
- 支持一键下载 GitHub zip、解压、生成 manifest；
- BDD100K 保留为高级大数据集路径；
- 新增 CamVid adapter，把 Road/Sidewalk RGB 语义标签转成 VQASee path guidance ground truth。

技术采纳分级：L2 最小实验。

下一次主动雷达主题：继续寻找更小、更明确 license、更贴近 VQASee 行走/户外风险的 open dataset sample pack。
