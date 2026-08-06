# Walking mode: local Vision trigger layer

Date: 2026-08-06

## Product decision

Walking mode is not a real-time video chatbot. It is a risk-first observation assistant:

- keep watching locally;
- send Qwen key frames when something important may have happened;
- stay quiet for stable, recent, low-signal frames;
- never treat local Vision as proof that the path is safe.

## Implemented MVP

The iPhone now runs a local fast signal before sending a walking frame to the backend:

- Apple Vision human rectangle detection (`VNDetectHumanRectanglesRequest`);
- a tiny 8x8 luminance fingerprint for scene-change scoring;
- brightness / likely-covered checks;
- a pure `WalkingFrameSendPolicy` with fail-open safety rules.

Walking mode sends a backend frame when:

- it is the first frame;
- the user asked a question;
- local Vision finds a possible person;
- image quality looks risky;
- the scene-change score crosses threshold;
- heartbeat reaches 6 seconds;
- local analysis fails.

Stable recent walking frames are skipped before Qwen to avoid spending seconds on stale/no-change video.

## Safety boundary

This layer is only a trigger hint. It does **not** identify stairs, curbs, holes, vehicles, or free space reliably. It must not produce a user-facing “safe to walk” decision by itself.

The local signal is sent to the backend context as `local_vision`; the backend prompt explicitly says it is not the final judgement and Qwen must confirm using the current image.

## Next steps

1. Measure real-device local Vision cost: p50 / p95 analysis time.
2. Add CoreMotion to distinguish stable straight walking vs. turning.
3. Evaluate a tiny Core ML detector for stairs / curb / vehicle only after collecting field samples.
4. Add UI/debug visibility for local skipped frames in a non-technical way if users need reassurance.
