# Camera buffer ownership: remove redundant source copies

## Change

Both FrameCaptureProxy and ARFrameCaptureProxy pass the source CVPixelBuffer
directly into their existing latest-frame queue. Swift strong references in the
pending tuple and processing closure keep the storage alive; no raw pointer is
stored. AR depth cues are extracted synchronously, without retaining ARFrame.
Consumers remain read-only; formats, orientation, model preprocessing, JPEG
encoding and generation-based stale-result suppression are unchanged.

Each proxy has one processing slot and one replaceable pending slot. Replacement
and reset release pending references. An executing synchronous Vision/encoding
operation releases its source when its closure finishes; reset cannot cancel an
already executing model call. Handoff can briefly overlap the completed closure
with the next queued closure, so this is not a strict global two-buffer budget.

AVFoundation didDrop logs the system attachment reason, separately from the
existing pending-frame replacement count. OutOfBuffers indicates source-pool
pressure and must not be treated as successful frame replacement.

## Review and acceptance

Product: preserve model outputs and preview, without claiming a measured speedup.
System: bounded ownership and synchronous downstream reads are prerequisites.
UI: no UI/protocol/schema changes. Model: preprocessing remains unchanged.

Real-device acceptance is pending: run the same scene on iPhone 17 through both
capture modes, with remote JPEG and diagnostic sampling enabled and disabled;
exercise stop/start and mode switches while inference is busy. Check preview,
overlay alignment, resultAgeMs p50/p95, memory and camera drop logs against the
previous build. Simulator compilation cannot establish source-pool safety.

If retaining sources causes pool exhaustion, replace the source copy with a
single required resize/conversion into an App-owned CVPixelBufferPool output;
release the source after conversion completes. Pool reuse must wait for every
consumer, including asynchronous GPU work. This fallback is not implemented
without evidence of source-pool pressure.

Scope excludes indoor photo persistence/JPEG decode and framework-internal copies.
No dependencies or installation requirements changed.

## Local verification

- `git diff --check`: passed.
- Generic iOS Simulator Debug build with signing disabled and isolated DerivedData:
  passed (`BUILD SUCCEEDED`). The repository build script's initial sandboxed
  attempt could not access CoreSimulator/normal DerivedData; the isolated build
  succeeded with access to Xcode services.
- No new mock tests: they would not prove camera-pool reuse or device behavior.
- Real-camera functional/scenario tests, capture drops, memory and latency
  comparisons: not run; require the iPhone 17 and a baseline capture session.
