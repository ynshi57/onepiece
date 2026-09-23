# 本地模式 UI：收起 AnswerPanel 黑框

## 乔布斯裁决

本地看路时**画面叠层是主产品**。「本地看路中」大玻璃黑框挡近端车道/障碍，属于噪声，应去掉。

## 思余改动

- `AssistanceScreen`：远程 VQA 关时不渲染 AnswerPanel；空闲仅一行 `ultraThin` 提示；观察中底部只留控制
- `StatusPill`：短连接文案作标题（如「本地看路中」），材质改 `ultraThinMaterial`
- `StreamingViewModel`：本地会话跳过「连接中」态，面板字段清空

## 验证

- xcodebuild simulator Debug：**BUILD SUCCEEDED**

更新时间: 2026-09-23 11:55
