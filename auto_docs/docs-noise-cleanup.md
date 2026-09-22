# 清除不再适用的文档噪声

## 最终结论

用户裁定：旧文档和不再适用的描述可以删，原则是清除噪声。Agent 还会当成「现在」的旧文删掉；按日期写的实验笔记本留下。

## 投票或共识

三审均为 `approve_with_changes`（[GPT](2eeec843-29b1-486b-b714-7d95e65d8237) / [Sonnet](eea4cb0f-86bc-42dd-8d11-fc912271fb73) / [Gemini](276cdf41-adf8-4e18-924f-71464da9c920)）。收窄后执行：8/27 只改时态；YOLO 覆盖召回 0.663 已在 `eval-baselines`，可删纪要；官方 skill 把「四模式当当前」一并改掉。

## 已执行

删整文件：

- `docs/vqasee-skills-draft/` 整树
- `docs/vqasee-codex-agent-design.md`
- `auto_docs/local-perception-gap.md` + `.json`
- `auto_docs/perception-four-questions.md` + `.json`

改现在时：

- `README.en.md` 对齐中文：TwinLite、视觉 overlay、CURRENT 指针
- `docs/performance/2026-08-27`、`docs/decisions/2026-08-27`：「live 仍二值」改成当时口径 + 指向 CURRENT
- `auto_docs/vqasee-core-goal-refocus.md` 待办不再问「是否仍二值」
- `.agents/skills/vqasee-self-evolution`：当前阶段改四项核心；决策例改视觉引导优先
- `docs/CURRENT.md` / `0001`：草案已删，只认 `.agents/skills/`

## 明确没删

`docs/model-lab/`、`docs/evolution/`、`docs/tech-radar/`、`docs/ui-lab/`、`docs/decisions/` 整篇、`docs/architecture/`。日期记录里出现「语音优先」是当时事实。

## 验证

- grep 活文档/技能：`voice-first`、`vqasee-skills-draft`、`仍二值` 已清空（CURRENT 解释句除外）
- 未跑 pytest（只动文档和一份 skill 文案）

更新时间: 2026-09-22 11:55
