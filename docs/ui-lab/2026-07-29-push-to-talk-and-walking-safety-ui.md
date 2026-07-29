# UI 经验：按住说话可诊断性与行走安全状态

## 背景

用户多次反馈按住说话不好使，容易出现“没听清 / 不能按住”。同时 walking 模式需要更像视觉辅助工具，而不是普通图像描述。

## 问题本质

- Push-to-talk：用户无法判断是按钮没按住、麦克风没进音、Speech 没返回，还是后端没回答。
- Walking UI：用户行走时不应该读长段描述，应先得到安全状态。

## UI 原则

1. 按住时必须有即时反馈：颜色、触觉、录音电平/波形。
2. 失败要分层：未授权 / 没声音 / Speech 失败 / 后端未答。
3. 行走主状态必须短：`可前行 / 放慢 / 停下`。
4. 风险优先读屏：VoiceOver 先读安全状态，再读方向，再读建议。

## 最小实验

- Push-to-talk：新增录音电平条，若 1 秒内电平低于阈值，提示“没有检测到声音”。
- Walking：从 risk_level + suggested_action 派生 safety_state：
  - low → 可前行
  - medium → 放慢
  - high → 停下

## 成功标准

- 用户按下按钮后 200ms 内看到“正在听”和电平变化。
- 如果没有声音，提示不是“没听清”，而是“没有检测到声音”。
- 行走模式主卡片第一行是安全状态，而不是长文本。

## 需要验证

- 真机麦克风电平是否可稳定读取。
- VoiceOver 下按钮和状态是否按正确顺序朗读。
- Dynamic Type 下底部控制区是否仍可触达。

## 2026-07-29 执行追加

已实现 push-to-talk 最小可诊断反馈：

- `SpeechRecognitionController` 在录音 buffer tap 中计算 0...1 音频电平。
- `StreamingViewModel` 记录 `speechInputLevel` 和峰值 `speechPeakLevel`。
- `PressToTalkButton` 按住时显示底部白色电平条。
- 如果 final transcript 为空且峰值太低，提示“没有检测到声音”；否则提示“没有听清”。

下一步：真机验证电平阈值 `0.08` 是否合理；如果过敏或过钝，再根据真实测试调参。
