# 05 — 诊断学习闭环

受众：所有人。这是提升准确率的核心。

## 为什么需要它

真实截图已经证明：YOLO 会把室内水桶/门边误检成车辆。  
这不是 UI 能解决的问题，也不能靠一句 prompt 解决。VQASee 必须形成真实数据闭环。

```mermaid
flowchart LR
    A[真实使用\n现场场景] --> B[用户开启诊断录制]
    B --> C[帧图 + 本地感知 JSONL]
    C --> D[Mac 离线分析]
    D --> E[误检\n漏检\n对齐错误]
    E --> F[模型 / 阈值 / prompt 改进]
    F --> G[真实样例回归测试]
    G --> H[新版本]
    H --> A
```

## 诊断包格式

```text
VQASeeDiagnostics/session-.../
  metadata.json
  manifest.jsonl
  frame-0001.jpg
  frame-0002.jpg
```

## 分析命令

```bash
python server-vqa/tools/analyze_diagnostic_capture.py /path/to/session
```

可选：离线重跑 Qwen。

```bash
QWEN_API_BASE_URL=http://127.0.0.1:11435 \
QWEN_MODEL=qwen2.5vl:3b \
python server-vqa/tools/analyze_diagnostic_capture.py /path/to/session --run-qwen --limit 20
```

## 闭环产出

- 真实误检样例；
- 真实漏检样例；
- 模型阈值；
- overlay 坐标对齐修复；
- 未来微调数据；
- 不能被假 mock 糊弄的回归测试。
