# 室内建图实验日记（2026-10-15 归档）

开工不要读本文件。状态卡在 `docs/SPATIAL_HANDOFF.md`。需要某一臂的数字或路径时再打开对应小节。

# 室内建图 A/B/C 接续入口

更新时间：2026-10-15。先读本文，不加载旧聊天全历史。用户已授权持续分析、实验、修复和真实验收；不自动提交/推送。默认单主代理，必要独立审查只传精简任务。

## 目标与状态

无LiDAR iPhone17，RGB+ARKit/IMU恢复可信矢量空间，Mac离线高斯外观。A定位/恢复/性能、B有限墙体/边界/拓扑、C实景外观**均未达标**。按真实照片→原因假设→单变量实验→逐图→准确度/耗时推进。保留226分母、失败、退步，不用训练/开发评测通过替代产品验收。

工作区 `/Users/wholewheat/opensource/onepiece`，分支`yn/dev_indoor`，HEAD `16a7ae1`。沿用全部未提交改动，不建worktree，不回滚用户/Cursor改动。旧gaussian_research已停工。

## 真实原件与已完成实验，勿重跑

私人原件（gitignored）：`server-vqa/diagnostic-captures/spatial-20261013/8EE9D885-BD00-49E7-94D0-7B775A526E68`。226图1920×1440、1516姿态、26平面，epoch2、225秒。Mac导入/照片页面已看图。
页面 `http://127.0.0.1:9013/spatial-scans/ui?scan=5ad0d221-4402-45b2-a3c8-a89f14442267`。

- 时间邻接：9组件、最大51相机；追加518非邻接候选：1组件、202相机、12935点、24未注册。45/202与AR传感器一致性条件冲突；不删除，不把对齐残差当物理真值。
- 全图SfM外观开发分区：175训练、27可评估，另5预定评估未定位仍记失败；不是独立定位/几何测试。
- 相机两臂真实Metal已完成：随机2000点、2000步/256/SH0/20k。AR18.4575dB/198.21秒；SfM18.7695dB/195.23秒。全部27图已看，两臂严重模糊，C未过。
- 稀疏初始化：12025点，每点≥2训练视图；910不满足条件，ID保留。RGB仅训练观测中位数，几何仍全图SfM。2000步/256/SH0/20k：21.39911dB、edgeF1 .64633、194.06秒、20000高斯，22升5降。全部27图看过；门/画架/远景仍失败。
- 同seed和20k，5000步预算对照：22.41475dB、edgeF1 .77427、469.07秒、20000高斯，22升5降；87画架-4.01dB、178-1.65。全部27图再看；C仍失败。增步数不是修复。

原运行目录：`/tmp/vqasee-gaussian-camera-control-20261014-metal`、`/tmp/vqasee-gaussian-sparse-control-20261014`、`/tmp/vqasee-gaussian-sparse-budget-5000-20261014`。
持久备份：`server-vqa/diagnostic-captures/spatial-20261013/{gaussian-camera-control,gaussian-sparse-control,gaussian-sparse-budget-5000}/`。
几何原目录 `/tmp/vqasee-new-scan-nonlocal-20261013/map/models/0`，原baseline `/tmp/vqasee-new-scan-replay-20261013/result.json`；缺失时查`diagnostic-captures/spatial-20261013/analysis/`，不擅造receipt或重采集。

## 已完成补充与当前运行

容量20k→50k（2000步）完成：22.58136dB、edgeF1 .77939、192.69秒、50000高斯，23升4降；全部27图已看。87退步3.72dB，门66/73/143仍严重拖影。`/tmp/vqasee-gaussian-sparse-capacity-50000-20261014`已备份同名类别目录，勿重跑。

原AR+相同12025稀疏seed完成：20.04387dB、edgeF1 .42045、183.34秒、20000高斯，3升24降；全部27图已看。`/tmp/vqasee-gaussian-raw-sparse-control-20261014`已备份`diagnostic-captures/spatial-20261013/gaussian-raw-sparse-control/`，两父dataset重建SHA核验、27指标重验和备份SHA均通过，勿重跑。

5000模型训练视角回导诊断：202图全部出图/指标，参数maxdiff2.384e-7，原27渲染最大差1色阶。175训练均24.18694dB；实际看24目标视角，门附近训练帧也差16–19dB，画架训练86/88/89为26–31dB但评估87为15.86。`/tmp/vqasee-gaussian-training-view-audit-20261014`已备份`gaussian-training-view-audit/`。只做1步lr1e-30/noise0，不是重训或纯零变化渲染。

225邻接对局部AR/SfM运动诊断：193可测/32缺注册保留。86→87与87→88不是大跳变；门/画架整段约5°/.1米全局分歧仍在（AR不是真值）。`local-camera-audit.json`已备份到`gaussian-sparse-control/`。

Brush越界隔离修补已完成：release构建10分41秒；原固定source/binary/生产receipt没改。同5000checkpoint同回导，27图逐像素相同，175训练仅78/191差1色阶，56.68秒。新2000步修补训练21.17333dB/.64518/385.09秒/20k，14升13降；全部27图已看，门/画架继续失败。两项已备份`gaussian-intersection-audit/`、`gaussian-intersection-training/`。源码只改render.rs；独立边界审查通过。原源码有额外receipt和pending权重，严格安装检查拒绝，研究审计逐个核验164源文件及shader并保留两额外文件SHA，不放宽生产检查。详见新技术雷达卡。

SH3实验已完成：21.89213dB/.67625/322.85秒/20k，20升7降，全部27图已看，门/画架未恢复。SH0/SH3同2000步各回导202图，175训练均21.61249/22.42385dB，54.35/109.95秒，参数maxdiff2.384e-7、原27PNG最多1色阶。额外18训练图已看，门SH3仍14–17dB。三项目录均已备份`gaussian-sh3-control/`、`gaussian-sh0-training-view/`、`gaussian-sh3-training-view/`。

训练射线夹角/覆盖诊断45视角完成，实际看8图，已备份`triangulation-support/`。门66/73/143夹角中位8–10°，不全是小视差；点多集中边沿/细线，画架87/178仅21/34点。不能当独立几何真值或唯一根因。

当前固定拓扑位置优化对照（不要重跑移动臂）：`/tmp/vqasee-gaussian-position-control-20261014`，原binary，12025点、无增密（refine_every2001）、无位置噪声，2000/256/SH0/20k。`moving`完成20.58477dB/204.97秒/12025，均值位移中位4.08mm/p95 8.68mm/max16.71mm，传感器对齐尺度非独立米真值。旧诊断误读PLY前三列为xyz（实际是SH颜色），已按字段名修正并保留invalidPreviousPositionDiagnostic，不能使用旧2米误值。

显式0学习率的`frozen`初始化失败101（调度器要求正数），失败保留。`frozen-nearzero`已完成：20.22808dB、edgeF1 .54254、262.15秒、12025点，命名xyz核对位移严格为0；相对moving为7升20降。全部27真值/移动/冻结三列图已看，门/画架仍失败。原result.json保留失败，corrected-result.json关联失败SHA及两完成臂。完整备份`gaussian-position-control/`并SHA核验，勿重训。

训练视角回导出现新异常，尚未通过：`/tmp/vqasee-gaussian-position-moving-training-view-20261014`及`/tmp/vqasee-gaussian-position-moving-patched-view-20261015`，1步全LR1e-30/noise0后，仅末点12024的f_dc_2分别改变.01158723/.00401816，旋转最多2.384e-7，其余参数不变。两次严格参数门均拒绝；即使原27PNG最多差1色阶也未放宽门槛。两项目录已完整备份至`diagnostic-captures/spatial-20261013/gaussian-position-moving-{training-view,patched-view}/`，各418文件SHA核验。尚不能归因于尾部越界、导出或优化器；PR373隔离修补未消除异常。冻结臂训练视角尚未回导。

完全不训练/不渲染的serde回环已复现：`/tmp/vqasee-tail-roundtrip-20261015`。真实12025点末点f_dc_2从-.00649703→0，合成12027点同位置从-.265625→0；1..5、12024、12026、12028、20000通过。每例同一Splats克隆连续两导出字节相同；仅证明这些样例纯读写可复现，不定位CPU解析/GPU上传/读回/布局转换。完整备份`gaussian-tail-roundtrip/`，包含原探针binary、源码、控制脚本、全部输入/导出与SHA核验。

第二探针`/tmp/vqasee-tail-roundtrip-readback-20261015`完成：原布局SH全元素准确，permute后读回才在12025/12027末点蓝色DC丢值；全部逐操作数据备份`gaussian-tail-roundtrip-readback/`。CPU重排隔离修补：`deploy/spatial/research/patches/brush-export-sh-cpu-layout.patch`，去导出GPU permute，按原布局读回并CPU重排SH字段；SH0/SH3各11例共22例双导出全字段通过，输出`/tmp/vqasee-tail-export-fixed[-sh3]-20261015`，备份`gaussian-tail-export-fixed[-sh3]/`。

当前构建修补后的隔离完整app：session93642，源码相对原base有render.rs(PR373)和export.rs(CPU重排)两处。原PR373-only binary保存在隔离根`brush_app-pr373-only`，旧experimental-receipt.json不要改；构建完成运行`/tmp/vqasee-export-fixed-receipt.py`生成独立export-fixed-receipt.json，验证164源文件和仅两处变化。然后运行`/tmp/vqasee-export-fixed-view-audit.py BASE OUTPUT 0`，先moving再frozen-nearzero同checkpoint回导，严格参数门不变；GPU串行，保留失败，27图及门/画架训练图需看。生产源码/app/receipt未改。底层GPU布局异常尚未修，只修export候选路径，C仍未过。
A/B实测尺寸已异步询问，未收到前不编真值。原binary `/tmp/vqasee-brush-verified-source/target/release/brush_app`；GPU串行锁。曾因自动审批额度拒绝一次运行，用户继续后正常审批重试成功，当前不是阻塞。

## 本轮修复与证据

`gaussian_camera_control.py`：补缩放像素转换/原照片尺寸检查；父源按原件重建并逐文件SHA验证，锁226/175/27、binary、实际预算、相机和分区；27渲染指标重算并支持输出SHA。旧父receipt缺预算摘要，以完整实际command核验，不补造历史字段；预检失败落盘，不覆盖旧输出。研究train_arm支持显式max_splats，默认20k不变，只用于容量对照。

14专测+相关配对/非邻接共22项通过；geometry安装profile通过。2000/5000真实Metal均完成。独立审查已做，指出的来源/预算/渲染/preflight缺口已补。

`support-audit.json`：87仅21支持轨迹/12个16×12网格，178为34/25，220为77/25；但73有522/114仍糊，不能全归点少。样本内SfM重投影p95约2.3–3.4原生px，不是独立真值。
`training-photometry-audit.json`：175训练图全部记录锁定曝光、duration=.01秒。172帧至少30支持点的亮度比p05 .819/p95 1.095、极值.609–1.221；RGB点差中位.0353。只训练RGB，没有应用增益或改图，不能断言自动曝光主因；反射/遮挡/匹配也会导致差异。

## 只按需读取

- 详细逐图/实验/角色裁决：`docs/model-lab/2026-10-14-spatial-sparse-initialization.md`。
- 相机理论：`docs/tech-radar/2026-10-14-gaussian-camera-control.md`。
- ABC未过门/依赖：`docs/evolution/2026-10-13-abc-causal-workplan.md`。
- 非邻接新扫描事实：`docs/evolution/2026-10-13-new-scan-nonlocal-connectivity.md`。
- 当前产品：`docs/CURRENT.md`开头室内条目，不必读全部历史。

环境`.venv/bin/python`，numpy/Pillow/scipy/pycolmap3.12.5已有，geometry profile通过。沙盒内Metal不可见，要使用已授权升级权限；别误判硬件不支持。仅离线研究，未改App/API/default/schema/依赖/验收标准，新结果未接默认页面。A真机20m/独立尺寸/重入/10分钟性能和B完整墙面仍需要真实独立验收证据。
