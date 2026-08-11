# VQASee Figma 线框规格

用途：给设计师或自己在 Figma / FigJam / Keynote 中重画展示稿。

画布建议：

```text
16:9 presentation
1440 x 810 px
```

全局样式：

```text
Corner radius: 24
Card padding: 28
Grid: 12 columns
Background: dark #0B0F14 or light #F7F8FA
```

## 组件库

### 1. iPhone Mock Frame

用途：展示 App 画面。

图层：

```text
iPhone Frame
Camera Preview Image
Detection Box Layer
Boundary Cue Layer
Voice Bubble
Bottom Control Strip
```

样式：

- iPhone 外框黑色；
- 摄像头画面圆角 36；
- 检测框橙/黄/红；
- 边界 cue 用黄色虚线；
- 语音气泡用半透明黑底。

### 2. Model Card

用途：展示模型职责。

图层：

```text
Icon
Model Name
Responsibility
Status Pill
```

状态颜色：

```text
已实现：绿色
原型中：黄色
未来：灰色
```

### 3. Risk Signal Chip

用途：显示风险/边界/不确定性。

例子：

```text
可能有车辆
疑似边界
近处疑似落差
正在确认
```

样式：

- 圆角胶囊；
- 黄/橙/红按风险等级；
- 文字简短。

### 4. Learning Loop Node

用途：诊断闭环。

图层：

```text
Circle Node
Short Label
Arrow Connector
```

节点：

```text
真实使用
诊断录制
离线分析
模型改进
回归测试
新版本
```

## 页面线框

### Page 1 — VQASee 是什么

布局：

```text
Left 55%: iPhone mock with overlay
Right 45%: title + 3 value cards
Footer: safety boundary
```

标题：

```text
VQASee：视觉风险辅助
```

三张 value cards：

```text
看见风险
即时提醒
持续变准
```

### Page 2 — 用户体验流程

布局：

```text
Horizontal journey, 4 cards
```

卡片：

```text
持续观察
本地风险信号
即时语音/触觉
语义解释
```

箭头：粗线，iOS 蓝。

### Page 3 — 系统分层架构

布局：

```text
Vertical stack of layers
Learning loop as side rail
```

层级：

```text
体验层
反馈策略层
感知层
语义层
采集传输层
```

右侧 side rail：

```text
诊断录制 → 离线评估 → 回归测试
```

### Page 4 — 模型职责地图

布局：

```text
Five model cards around LocalPerceptionSignal center
```

中心：

```text
LocalPerceptionSignal
```

周围：

```text
Apple Vision
YOLO11nObject
Road Boundary
Depth / LiDAR
Qwen
```

### Page 5 — 诊断学习闭环

布局：

```text
Large circular loop
Case callout on right
```

右侧案例：

```text
水桶误检成车辆
→ 保存真实帧
→ 离线分析
→ 加入回归样例
```

### Page 6 — Road Boundary 路线图

布局：

```text
Three-stage roadmap
Current / Prototype / Future
```

三列：

```text
当前：检测框和风险标签
原型：道路边界模型 + 深度
未来：可信边界和候选通行区域
```

底部红线：

```text
不承诺“可以走/可以开”
```

## 输出建议

- Figma 用于设计细节；
- Keynote 用于讲故事；
- Markdown 作为内容源；
- 不要直接把工程流程图搬进展示稿。
