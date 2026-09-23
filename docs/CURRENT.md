# VQASee 现行宪法 · CURRENT

- 生效：2026-09-22
- 来源：用户逐条拍板（docs 知识记忆审计）
- 冲突规则：**本页优于 `docs/decisions/` 里的 dated 裁决。** `AGENTS.md` 管长期工作方式；产品「今天是什么」以本页为准。
- 8/10 对话不是现行知识。非正式 skill 草案已删除；正式工作流只认 `.agents/skills/`。

## 定位

视觉引导优先，语音辅助确认。不是语音优先产品，也不是低视力默认形态。低视力可访问性仍要做。不承诺「可以走 / 可以开」。

代码指针：`AGENTS.md` 不可妥协原则 2。旧文「语音优先」现在时作废。

## 当前阶段做什么

只做户外街景四项：车道线、按人/车区分的可通行区、障碍物、实时性。

- **留下：** 连不上、超时、安全变化被语音吞掉——坏了必须看得见。
- **默认会话：** 打开 App 即可本地看路（TwinLite + YOLO 叠层），**不需要**连 Mac。远程 Qwen VQA（文字解释 / 问答）在设置「远程风险解释」里显式开启后再发现并连接后端。
- **场景边界（2026-09-23 咖啡店实测）：** TwinLite/可走区/车道叠层**只承诺户外街景**。咖啡店、商场等室内会喷假车道与假可走区——这是域外失效，不是「再调阈值」能修好。产品形态：室内应诚实收叠层或明示「户外看路模型不适用」，不能假装在导航。
- **实时性抓手：** 相机预览是连续视频；本地感知是**节流离散帧**（目标约 5 Hz，`minLocalPerceptionInterval=0.2s`），不是把整路视频流灌进模型。旧值 2.0s 会让叠层像「画面不动、线乱跳」。远程上送仍 2.0s。
- **以后再做：** 读文字、看周围 2 秒、设置页切 3B/7B、Qwen 问答当主叙事；室内场景路由 / OOD 门闩。

生效补充：2026-09-23（本地感知默认 · 远程 VQA 设置开启；室内边界与感知帧率拆分）。

## Live 路面模型

**TwinLiteNet 是现在的产品默认。** 打开 App 跑 `PerceptionConfig.default.roadBackend = .twinlite`。

- 二值 Fast-SCNN 已退役，禁止再写「shipping 仍二分类」。
- mc5 已 bundle，开关默认关；角色可走（人行道 vs 马路）还没翻到 live。
- TwinLite 不分人/车。四项核心第 2 条仍是承诺，live 尚未兑现。

代码指针：`PerceptionConfig.default`、`RoadSurfaceBackends.make`。

## 车道线

- **现在用户看见的车道：** TwinLite 抽出来的几何折线（不是 128×96 黄格子）。没有折线就明示没有。格子只当调试层。
- **下一阶段：** UFLDv2 真·几何折线。decoder 在代码里；有权时优先 UFLD，否则 TwinLite 折线。live `lanePolylineRunner` 仍可能为 nil。
- **CamVid 像素车道：** 只做评测资产，不是产品车道线。

## 三区 ROI

左 / 近 / 右三个框不是产品信号。屏幕上的框已去掉。引擎、OTA、Qwen `walking_roi` ingest、case 层、`path_roi.py`、701 帧 jsonl 旧字段、harness-config 矩形都已删干净。旧 payload 里的 `walking_roi` 键会被忽略，不再写入 prompt。`frame_quality` 模糊/曝光短路仍保留。VQA `direction` 的 `left_front` / `right_front` 是空间方向，不是三框。新 manifest 只写 `traversable_grid`。

## 障碍 · 台阶 / 路沿

产品仍要提示台阶和路沿。**不要给 YOLO11n 加这两个类名**——COCO 没有，加字符串没用。缺口写在这里，靠以后的边界 / 深度，不靠改检测标签。

## 评测尺子

- 可走区、障碍：CamVid（及后续公开集）仍可用。
- 产品车道线：不以 CamVid 像素当唯一真值。
- UFLDv2 阶段：CULane 一类「线」标注。

## 明确不是什么

- 不是 Qwen 聊天助手。
- 不是自动驾驶。
- 诊断台、CamVid 701 帧、harness 是进化工具，不是第四项核心能力。
- `auto_docs/` 是工作纪要；同日多份以最新且带用户拍板的为准。

## 本页怎么改

改产品规定就改本页，并写拍板日期。不要靠改 8 月决策正文来「更正历史」。
