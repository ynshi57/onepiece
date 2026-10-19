# Brush SH 导出隔离修补

`brush-export-sh-cpu-layout.patch` 针对固定上游提交
`3edecbb2fe79d3e2c87eeab85b15e0b1dd10d486` 的 `brush-serde/src/export.rs`。
这是研究候选，未接入安装脚本或生产 receipt，不能直接替换已验证的生产二进制。

2026-10-15 真实 Metal 零训练回环发现：SH0 的 12025/12027 点样例，原布局
SH 张量读回准确，但 `permute([0, 2, 1])` 后读回的末点蓝色 DC 丢失。
修补直接读回 `[point, coefficient, channel]`，在 CPU 按 PLY 的通道顺序
重排全部 DC/rest 系数，避免这一导出路径上的 GPU 布局转换。
不丢点、不改系数、不放宽验证门槛；没有证明底层 GPU 异常根因或画质恢复。

在隔离的固定源码副本中使用 `git apply --check` 后应用 patch。
验收必须覆盖 SH0/SH3、1..5、12024..12028、20000 点，逐系数/通道使用
不同的精确二进制分数，对比全部命名 PLY 字段、连续两次导出和原布局读回。
随后必须用同 checkpoint 做真实回导，保留参数和图像严格门槛。
实验细节、失败与产品边界见 `docs/model-lab/2026-10-14-spatial-sparse-initialization.md`。
