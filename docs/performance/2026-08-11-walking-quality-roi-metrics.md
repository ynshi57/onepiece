# Walking quality gate / ROI metadata / backend metrics

Date: 2026-08-11

## Decision

Walking frames now support validated `frame_quality` and `walking_roi` metadata. High/medium-confidence quality failures can short-circuit Qwen with a cautious user-visible warning. ROI is used as prompt metadata only; it does not crop the image or hide off-ROI safety risks.

## Why

The previous walking fast schema reduced output length, but Qwen visual prefill can still dominate latency. A user should not wait for Qwen to learn that the frame is blurry, too dark, or covered. At the same time, hard ROI cropping is unsafe before A/B evidence because side-entering people, vehicles, and opening doors may be outside the near-path region.

## Safety boundary

- Quality gate only short-circuits `walking` frames without an explicit user question.
- Only `medium` / `high` confidence unusable quality triggers short-circuit.
- The response is cautious (`risk_level=medium`) and tells the user to slow down or adjust the phone.
- ROI metadata explicitly tells the model: focus on ROI but do not ignore off-ROI people, vehicles, opening doors, stairs, curbs, or edges.
- No meter-level distance fields are introduced.

## New metadata

```json
{
  "frame_quality": {
    "blur": "ok|blurry|unknown",
    "exposure": "ok|too_dark|too_bright|unknown",
    "occlusion": "ok|covered|unknown",
    "usable_for_walking": true,
    "confidence": "low|medium|high",
    "spoken_hint": "画面有些糊，请放慢。"
  },
  "walking_roi": {
    "coordinate_space": "normalized_image",
    "near_path": {"x": 0.2, "y": 0.45, "w": 0.6, "h": 0.55}
  }
}
```

## Diagnostic metrics

Fused responses may include `diagnostic_metrics` for lab/debug use:

- `qwen_http_ms`
- `worker_total_ms` or `direct_total_ms`
- `frame_base64_bytes`
- `schema_name`
- `fast_response`
- `incremental`
- `quality`
- `walking_roi_present`
- `quality_gate=short_circuit` when Qwen was skipped

These metrics are not user-facing speech text.

## Validation

2026-08-11 backend validation:

```bash
source .venv/bin/activate && pytest server-vqa/tests/test_frame_metadata.py server-vqa/tests/test_worker_client.py server-vqa/tests/test_signaling.py server-vqa/tests/test_vqa_service.py server-vqa/tests/test_fusion.py server-vqa/tests/test_api.py
```

Result: 57 passed.

Next required validation:

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

Then true product validation requires 30–50 real/near-real walking frames and p50/p95 latency reporting.

## 2026-08-12 Latest-frame-wins P0

Problem: in local Mac 16GB + Qwen runtime, Qwen is a slow verifier, not a realtime eye. Previous behavior dropped every camera frame captured while a request was in flight; diagnostics showed sessions with >70% `captured_while_in_flight` frames.

Decision: add iOS-side latest-frame-wins before attempting deeper worker cancellation.

Behavior:

- When a backend/Qwen request is in flight, iOS keeps only the latest captured frame in memory (`pendingLatestFrame`).
- Newer frames replace older pending frames; no queue grows.
- Local immediate feedback still runs while Qwen is busy, so users can hear fast local risk hints.
- When the current Qwen result returns or times out, iOS immediately sends the retained latest frame if streaming is still active.
- Diagnostic upload still records `captured_while_in_flight`, with reason `backend busy; latest frame retained for next send`.

Safety boundary:

- The running Qwen request is not forcibly cancelled in this P0; stale result suppression is still a future step.
- Pending frames are cleared on stop/disconnect/worker offline.
- Single-shot voice questions do not keep the stream alive just to drain pending frames.

Validation:

- Swift business source typecheck passed with existing Swift 6 concurrency warnings.
- Backend regression passed: `source .venv/bin/activate && pytest server-vqa/tests` → 92 passed.

Next:

- Add frame IDs to VQA results and ignore stale model results when a newer frame has already superseded them.
- Store latest-frame replacement counts in diagnostics/report metrics.
- Add backend/worker low-frequency Qwen policy and raw output capture.
