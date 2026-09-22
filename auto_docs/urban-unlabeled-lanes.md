# 无标注城市车道：怎么办（乔布斯）

## 最终结论

用户目标是**各种不带标注的街景**上，实时准的车道线 + 障碍 + 引导线。退回 CamVid 自带车道标注画图 18，**不能**当产品方案。

已关：

- CULane-UFLDv2 当城市产品轨（01TP/06R0 未过看图门）
- 把图 18 退回 GT 教师
- 再跑 CurveLanes（2026-08-29 已因幻象拒绝）

已做最小实验：TwinLiteNet BDD 预训练，只吃 RGB，禁止合并 CamVid Lane GT。对照帧看过图 18 和 18c。

**视觉门未过。** 01TP 失败。06R0 弯道第一次由模型（不是标注）画出接近司机看见的边，但不能覆盖「各种街景」。

本机 Intel i5，`cuda=False`，`mps=False`，**不能微调**。下一真实步骤需要 NVIDIA GPU + 城市车道线实例数据。障碍继续 YOLO。App / 155MB bundle 仍不做。默认 `occupied_lane_rails` 未切到 TwinLiteNet。

## 投票 / 共识

关三条、开 TwinLiteNet 叠图：GPT / Sonnet / Gemini / Opus 赞成。子代理额度不足，审阅由主 Agent 按三份清单写出并归档。

## 看图（2026-09-21）

| 帧 | 18c 原 mask | 图 18 产品预览 | 门 |
|---|---|---|---|
| 0001TP_008430 | 红块只在车头近处；绿线是斜杠和碎段，到不了骑行者 | 黄轨跟红块走成钩子，蓝线短，占用对是牙子-牙子 | 未过 |
| 0006R0_f00960 | 红区盖住本车道弯；绿线贴左虚线和右牙子，对面车道也有绿 | 黄左沿虚线弯、黄右沿牙子弯，蓝夹在中间；起点偏右，对面还有黄碎段 | 部分像，整体未过 |

根因：TwinLiteNet 是 BDD 可行驶区+车道**像素**，不是车道实例。城市峡谷/低光（01TP）域差。把预测像素送进 `rails_from_lane_mask` 只能画出 mask 里已有的边，mask 错则轨错。

## 已执行

- `server-vqa/app/twinlite_net.py`：RGB → lane/DA mask
- `occupied_lane_rails(..., mask_source=)`；`twinlite` 时不跑 UFLD、不合并 GT
- 渲染：`--paint-source twinlite`，产物图 18 + `18c-twinlite-masks.jpg`
- pytest `test_lane_rails.py` 25 passed

## 待办 / 阻塞

**真实阻塞（只有用户能解）：** 一台有 NVIDIA CUDA 的机器，或拿回已在城市线实例数据上微调好的权（OpenLane / BDD lane / 自采 iPhone 折线）。这台 Mac 停训。

旁路已做完：障碍仍走 YOLO11n；引导仍是轨间 G2；不过门就不改 App 默认路径。

不在本轮：再调像素阈值、再下 CurveLanes、把 TwinLiteNet 设成默认。

## 诊断台 12 帧验证（2026-09-21）

用户：停实时性讲课，继续在平台上看 TwinLiteNet 效果。不是 App 默认。

入口：http://127.0.0.1:9000/diagnostics/twinlite/ui  
（总览 / 数据集评估页有卡片）

已看完全部 12 张 test split（红=DA，绿=车道像素，蓝=占用车道引导线）。**视觉门仍未过，不能当产品默认。**

| 序列 | 帧 | 看图 | 门 |
|---|---|---|---|
| 0001TP 低光窄街 | 006690 | 红只在车头，绿碎段，蓝短钩 | 未过 |
| 0001TP | 008430 | 红近处团，绿是团边不是到骑行者的车道，蓝钩 | 未过 |
| 0001TP | 009240 | 红几乎没有，蓝线缺失，绿贴人行道 | 未过 |
| 0006R0 开阔 | f00960 | 红盖本车道弯，绿贴虚线/牙子，蓝沿弯 | 部分过 |
| 0006R0 | f02550 | 超市车道，蓝扭向车位 | 未过（场景也不是行车标线） |
| 0006R0 | f03450 | 蓝 S 钩，红涂到斜线车位 | 未过 |
| 0016E5 | 04530 | 红偏对向，蓝短截顶在金车屁股上，没停障碍 | 未过 |
| 0016E5 | 06630 | 红走廊对，绿贴停车边缘，蓝扭但还在路里 | 部分像 |
| 0016E5 | 08047 | 红盖自行车道+车道，蓝在车行道扭，骑行者未停笔 | 部分像 |
| Seq05VD | f01080 | 红在路里，绿左牙子/右停车，蓝扭 | 部分像 |
| Seq05VD | f02820 | 同上，路口前蓝扭 | 部分像 |
| Seq05VD | f04080 | 红两车道，绿贴两边停车，蓝在本车道扭，对向也涂红 | 部分像 |

规律：低光堵车（0001TP）模型近处塌成一团；白天走廊可行驶区大体对，但绿线是像素边不是实例车道，蓝线按中线扭；障碍 YOLO 没进这张预览所以不会停笔。

## 已执行（平台）

- `/diagnostics/twinlite/ui` 串行出图（避免 12 路 CPU 打满）
- `/diagnostics/twinlite/frame?stem=` 缓存 `~/.cache/vqasee/twinlite-preview`
- 对照拷贝：`docs/model-lab/figures/twinlite-preview/*.jpg`
- pytest：`test_twinlite_preview.py` + 相关 41 passed

## 关键路径

- 权重：`~/.cache/vqasee/models/twinlitenet_bdd.pth`（1.8MB）
- 结构：`~/.cache/vqasee/ext/TwinLiteNet/model/TwinLite.py`
- 图：`docs/model-lab/figures/camvid-0001TP_008430/18-persp-structure-reproject.jpg` 与 `18c-twinlite-masks.jpg`（06R0 同目录）

## 风险与回滚

- 图 18 文件已被 TwinLiteNet 叠图覆盖；要对照旧 UFLD/GT 需重跑 `--paint-source auto` 或 `gt`
- TwinLiteNet 未进 `setup_mac.sh`（实验未过门）

## 18c 叠引导线（2026-09-21 09:52）

用户要在红绿图上看蓝线。已把占用车道 G2 叠进 `18c-twinlite-masks.jpg`。

看图：06R0 蓝线夹在左右绿边里沿弯走，起点偏右下，停在黑车前。01TP 蓝线只在近处红团里拐钩，到不了骑行者。产品结论不变：开阔弯道能看，窄街不能上。

更新时间: 2026-09-21 11:15
