# Walking mode async pipeline: ring buffer / latest-frame-wins plan

Date: 2026-08-06

## Decision

Do **not** immediately drop late Qwen results in walking mode. Qwen 3B can still take 4-5 seconds per key frame; hard expiry would make the app silent too often.

Instead, walking mode should evolve toward a two-lane pipeline:

1. **Immediate lane**: iPhone local Vision / haptics / short speech, sub-second.
2. **Semantic lane**: Qwen key-frame explanation, slower but richer.

## Proposed engineering optimization

### iOS frame side

- Keep a small ring buffer of recent frames and local signals.
- Use `latest-frame-wins` for backend inference: if Qwen is busy, retain only the latest candidate key frame instead of queuing many old frames.
- Preserve the current in-flight request until it returns; do not start unbounded parallel requests.
- When the in-flight result returns, immediately send the latest pending key frame if one exists.

### Backend side

- Keep request queue size at 1 for walking mode.
- Surface `dropped_due_to_newer_frame` / `replaced_by_newer_frame` counters in debug status.
- Track latency split: encode, network/relay, queue, model, speech.

### User experience

- Local immediate feedback can say: “正前方可能有人，我正在确认。”
- Qwen later confirms/explains: “正前方有人，请稍微向左。”
- If Qwen is slow, the user still heard the local safety hint.

## Not doing yet

- No hard TTL drop for Qwen results yet.
- No unbounded multi-threaded Qwen inference; on 16GB Mac this would likely worsen latency and memory pressure.
- No custom Core ML detector until field data justifies target classes.

## Validation target

- Local immediate feedback p95 < 300ms.
- Walking mode backend queue depth <= 1.
- No repeated old-frame speech after a newer high-risk local signal.
- Qwen semantic result still visible/audible when useful, even if slow.
