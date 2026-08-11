# Diagnostic upload and offline analysis

Date: 2026-08-06

## Why

Field screenshots showed two product-critical problems:

- fixed blue guidance lines looked like a real trajectory but were not model-driven;
- YOLO11nObject misclassified an indoor bucket/door edge as a vehicle.

This means visual overlay alone is not enough. VQASee needs a repeatable capture → offline analysis → model/pipeline improvement loop.

## iOS diagnostic upload

In the app:

1. Connect to the Mac backend.
2. Open Settings.
3. Turn on `上传诊断帧`.
4. Reproduce the issue for 10-30 seconds.
5. Turn off `上传诊断帧`.

The app uploads compressed JPEG frames and local perception metadata to the currently connected Mac backend over WebSocket. It does not keep a long-term local iPhone copy.

The backend writes:

```text
server-vqa/diagnostic-captures/session-.../
  metadata.json
  manifest.jsonl
  frames/
    frame-0001.jpg
    frame-0002.jpg
```

Each manifest row includes:

- mode / question;
- event: `sent_to_backend`, `skipped_before_backend`, `captured_while_in_flight`;
- encode time;
- local vision status;
- YOLO/local perception objects;
- road and depth cues;
- backend context string.

Privacy: upload is off by default. Users/testers must explicitly enable it, and frames are sent only to the current Mac backend.

## Mac offline analysis

Find uploaded sessions:

```bash
curl http://127.0.0.1:9000/diagnostics/sessions
```

Then run:

```bash
python server-vqa/tools/analyze_diagnostic_capture.py /path/to/session
```

To also rerun Qwen on saved frames using the current backend/runtime configuration:

```bash
QWEN_API_BASE_URL=http://127.0.0.1:11435 \
QWEN_MODEL=qwen2.5vl:3b \
python server-vqa/tools/analyze_diagnostic_capture.py /path/to/session --run-qwen --limit 20 \
  --output /tmp/vqasee-offline-report.json
```

## What to inspect

- False positive objects, e.g. bucket detected as car.
- False negatives, e.g. real bicycle not detected.
- Direction mismatch: object is left but tagged center/right.
- Frames captured while backend was still in-flight.
- Whether local perception and Qwen disagree.

## Product rule

Do not re-enable fixed trajectory lines as default. Guidance/corridor overlays must come from model/depth/road-boundary outputs or be explicitly labeled as debug.

## Backend management API

```text
GET  /diagnostics/root
GET  /diagnostics/sessions
GET  /diagnostics/sessions/{session_id}
GET  /diagnostics/sessions/{session_id}/frames/{frame_name}
POST /diagnostics/sessions/{session_id}/labels
```


## Web 标注台

打开本地后端页面：

```text
http://127.0.0.1:9000/diagnostics/ui
```

功能：

- 查看已上传 session；
- 打开 session 标注页；
- 查看每帧图片；
- 标注 `correct` / `false_positive` / `missed` / `wrong_class` / `bad_box` / `stale_result` / `other`；
- 写备注，例如“水桶误检成车”；
- 标注写入 `labels.jsonl`。

离线分析脚本会输出 `label_summary`，用于统计已标注帧和标签类型分布。

## 数据清理

删除单个 session：

```bash
curl -X DELETE http://127.0.0.1:9000/diagnostics/sessions/<session_id>
```

清理旧 session：

```bash
curl -X POST 'http://127.0.0.1:9000/diagnostics/cleanup?older_than_days=7'
```

## 指标边界

当前 `label_summary.coarse_metrics` 是粗粒度标注指标：

- `correct` 近似 true positive；
- `false_positive` / `wrong_class` / `bad_box` 近似 false positive；
- `missed` 近似 false negative。

这不是 bbox IoU，也不是像素级 segmentation 指标。道路边界、台阶、坑洞仍需要更严格的 ground truth 和专门指标。
