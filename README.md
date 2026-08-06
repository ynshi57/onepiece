# iPhone Realtime VQA (Phase 1)

This repository hosts the first phase implementation for a visual-assistance VQA
app:

- iOS app workspace and release automation under `ios-vqa-app` and `deploy/ios`
- Local Mac VQA backend under `server-vqa`
- Public WSS/WebSocket relay MVP under `relay-server` for cross-network use

## Quick Start

1. Install dependencies:
   - `bash deploy/ios/install_deps.sh`
   - If fastlane step fails, upgrade Ruby to >= 3.2 first.
2. Run backend tests:
   - `source .venv/bin/activate && pytest server-vqa/tests`
3. Start backend service:
   - `bash ./start_backend.sh`
   - Optional: `HOST=127.0.0.1 PORT=9000 bash ./start_backend.sh`
   - Optional local Qwen config (16GB friendly): `QWEN_API_BASE_URL=http://127.0.0.1:11435 QWEN_MODEL=qwen2.5vl:3b bash ./start_backend.sh` (the local runtime listens on `:11435`; see "Local Qwen Setup" below)
   - WebSocket signaling endpoint: `ws://localhost:9000/ws/signaling`

## Cross-network Relay MVP

Use this when the iPhone is on 4G/5G and the local Mac VQA worker is on Wi-Fi.
Both sides make outbound WebSocket connections to a public relay, so no router
port forwarding or shared LAN is required.

1. Start relay on a public host (or locally for testing):
   - `export RELAY_PAIRING_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"`
   - `bash ./start_relay.sh`
2. Start local worker on the Mac that runs inference:
   - `export RELAY_WORKER_URL=ws://<relay-host>:9100/ws/worker`
   - `export RELAY_PAIRING_TOKEN=<same-token>`
   - `export WORKER_ID=local-mac-worker`
   - `bash ./start_worker.sh`
3. In iOS app:
   - Server URL: `ws://<relay-host>:9100/ws/client` or `wss://.../ws/client`
   - Pairing token: same `RELAY_PAIRING_TOKEN`
   - Worker ID: same `WORKER_ID`
   - Client ID: any stable name such as `bayes-iphone`

MVP relay limits:

- Max frame Base64 bytes: `900000`
- Max frames per minute per client: `30`
- Max in-flight requests per client: `1`
- iOS default frame interval: `2s`
- iOS frame quality is mode-aware: 行走 448px/120KB, 周围 640px/220KB, 详细 768px/320KB, 读文字 1024px/520KB

## Local Qwen Setup (free/local)

1. One-step prepare local Qwen model + runtime:
   - `bash ./start_qwen_local.sh` (starts on `http://127.0.0.1:11435`)
   - Sub-commands: `start` (default) · `stop` · `status` · `supervise`
     (foreground, restart-on-crash).
2. Start backend with Qwen enabled:
   - `QWEN_API_BASE_URL=http://127.0.0.1:11435 QWEN_MODEL=qwen2.5vl:3b bash ./start_backend.sh`
3. Or run full stack in one command:
   - `bash ./start_local_vqa.sh`

### Runtime: direct `llama-server` (not Ollama)

By default `start_qwen_local.sh` launches **`llama-server` directly** (the binary
bundled inside `Ollama.app`) instead of letting Ollama manage it. The reason is
latency: Ollama derives `--image-min-tokens` from the model's baked vision config
(**1024** for `qwen2.5vl`) and exposes no env var or Modelfile `PARAMETER` to
lower it. That visual-token floor is the dominant prefill cost. Running
`llama-server` ourselves lets us pass `--image-min-tokens 256 --image-max-tokens
512`.

Measured on the same 448px frame (M4 Air, 16GB):

| `image-min-tokens` | prompt tokens | **prefill** | decode |
|---|---|---|---|
| 1024 (Ollama default) | 1048 | **~5.0 s** | ~1.4 s |
| 256 (this runtime) | 280 | **~1.3 s** | ~1.2 s |

Prefill drops ~4× with no measurable quality loss for scene description. Ollama
is still used purely as the **model downloader** (`ollama pull` writes the blob
store the runtime reads).

- **Config** (env overrides): `LLAMA_PORT` (11435), `IMAGE_MIN_TOKENS` (256),
  `IMAGE_MAX_TOKENS` (512), `LLAMA_SERVER_BIN`, `OLLAMA_MODELS_DIR`,
  `MODEL` (`qwen2.5vl:3b`).
- **Fallback to Ollama**: set `USE_OLLAMA=1` to use the old Ollama-managed
  runtime (image-min-tokens locked at 1024, API on `:11434`). Everything
  downstream is unchanged; only the API base differs.
- **Crash recovery**: `bash ./start_qwen_local.sh supervise` runs a foreground
  supervisor that restarts `llama-server` if it exits, logging each restart
  (No Silent Failures). Logs at `/tmp/qwen-llama-server.log`, pid at
  `/tmp/qwen-llama-server.pid`.
- **Warmup**: after the server is healthy, `start_qwen_local.sh` fires one tiny
  32×32 JPEG inference so the vision path is hot before the first camera frame.
  Best-effort and bounded by `--max-time` — failures are logged, not fatal.
- **Decode length**: walking / surroundings frames without an explicit question use
  a compact safety schema and `QWEN_MAX_TOKENS_FAST` (default 260); full
  descriptions use `QWEN_MAX_TOKENS_FULL` (default 520).
- **Continuity cost**: incremental frames default to current image + text scene
  context only. Previous-image comparison is opt-in via
  `QWEN_SEND_PREVIOUS_IMAGE_IN_INCREMENTAL=1` because sending two images roughly
  doubles vision prefill pressure on Qwen 3B.

> Honest expectation: the direct runtime brings a single 3B frame to ~2.5 s on a
> 16GB Mac (prefill ~1.3 s + decode ~1.2 s) — it does **not** reach 1s for a
> full-frame inference. The sub-second *feel* comes from the scene-memory gating
> below: most stationary frames are never re-inferred or re-spoken.

## Real Device Networking Notes

- On physical iPhone, `localhost` points to the iPhone itself, not your Mac backend.
- Use one of:
  - recommended nearby mode: turn on iPhone Personal Hotspot, connect Mac to that hotspot, start backend, then tap "开始视觉辅助" in the app
  - same Wi-Fi LAN URL: `ws://<mac-lan-ip>:9000/ws/signaling`
  - Mac connected to iPhone hotspot URL
  - public tunnel URL (e.g. `wss://...`) when phone uses cellular and Mac uses different network

## Nearby Auto-connect Mode

For the simplest local setup:

1. Start backend on the Mac: `bash ./start_backend.sh`.
2. Put the iPhone and Mac on the same network — **either** works:
   - iPhone Personal Hotspot with the Mac joined to it, **or**
   - both on the same Wi-Fi LAN.
3. Open VQASee on iPhone. It auto-discovers the backend and shows
   `已发现 Mac 后端…`.
4. Tap `开始视觉辅助` to connect (discovery only fills the address; you still tap
   start — the camera never auto-streams).

How discovery behaves:

- The backend advertises `_vqasee._tcp` via Bonjour; the app browses continuously
  (not just on first launch). Resolution **prefers the numeric IPv4 address** over
  the `.local` hostname, which is more reliable across routers where mDNS name
  resolution is flaky.
- **One backend found** → its address is auto-filled.
- **Two or more found** → the app shows a selection list so you can pick which Mac
  to use; your pick is remembered and no longer overridden by discovery.
- **Connection drops / you switch networks** (hotspot ↔ Wi-Fi) → the app clears the
  stale address, re-runs discovery, and reconnects to whatever it finds, instead of
  pinning the old IP.
- If Bonjour finds nothing, the app also probes common iPhone-hotspot addresses
  such as `172.20.10.x`.
- Manual entry still exists under **高级设置** as a fallback; typing an address
  there pins it (discovery won't overwrite it).

No server URL, relay URL, or token needs to be entered for this nearby/hotspot
mode.

## Visual-assistance UI

The iOS app is now voice-first:

- Main screen shows a natural-language summary, risk message, and suggested action.
- End-to-end latency is shown as a dedicated row (⏱) in the result card, broken
  down into encode / network+queue / model. The **previous** latency value stays
  on screen while the next frame is in flight (a small "更新中…" spinner marks the
  refresh) instead of blanking to "处理中…". The camera-preview overlay now shows
  only status + connection (smaller font); latency lives in the result card.
- If a frame gets no result within ~50s (model too slow, or the connection
  dropped), the app stops waiting, shows a red timeout message, and releases the
  in-flight lock so the next frame can be sent — it no longer hangs on "处理中…".
  In relay mode the relay also actively tells the client when its request expired
  (`request_timeout`) instead of dropping it silently.
- If the backend goes away mid-session (server stopped, network lost), the socket
  drop is surfaced as `连接已断开`, the status/connection text updates immediately,
  and after ~2s the app re-runs Bonjour discovery and reconnects to whatever it
  finds (so a hotspot ↔ Wi-Fi switch recovers instead of pinning the dead IP) — it
  no longer sits silently on "连接中".
- `scene / objects / latency` are kept in Advanced Settings as debug details.
- Voice output uses iOS `AVSpeechSynthesizer`; no third-party speech library is
  required.
- Frame encoding is mode-aware: `行走` uses 448px/120KB for latency, `周围` uses 640px/220KB, `详细` uses 768px/320KB, and `读文字` uses 1024px/520KB for OCR/detail.
- The app can switch between `自动`, `qwen2.5vl:3b` and `qwen2.5vl:7b`.
  - `自动`: 行走 uses 3B; 周围/详细/读文字 use 7B.
  - `3B`: faster, good for continuous walking mode.
  - `7B`: usually better scene/spatial understanding, but slower and needs more RAM.
  - Before selecting `7B`, pull it once on the Mac:
    `MODEL=qwen2.5vl:7b bash ./start_qwen_local.sh`
- Modes:
  - `周围`: low-frequency scene awareness with left/center/right spatial layout
  - `行走`: risk-first walking hints with direction-aware next action
  - `读文字`: single-shot text reading prompt
  - `详细`: single-shot detailed description
- **Scene memory & change-only reporting** (continuous modes): when you stay in
  one place, VQASee should not keep repeating the same description — it speaks the
  first frame fully, then only announces *important changes*.
  - The backend stays **stateless**. Each frame, the iOS app echoes back a small
    `context` object (its own previous summary/scene/objects, a GPS-derived
    `place_label`, and elapsed time). A stateless prompt-assembly step
    (`scene_context.build_contextual_prompt`) appends a 连续观察上下文 block so
    the model reports deltas and returns `change_significance` (`none`/`minor`/
    `major`) plus a short `changes` string. This context travels over both the
    direct and relay inference paths.
  - The app only speaks when the change is `major`, when risk **rises**, or when a
    max-silence heartbeat (~25s) elapses — otherwise it silently refreshes the
    screen. A voice question always forces a spoken answer.
  - **GPS reverse-geocoding** (physical anchor): on-device `CLGeocoder` turns the
    coordinate into a place label (e.g. `中关村南路附近`), re-queried only after
    moving ~30m (throttled, cached, off the inference critical path). Geocoding
    failures are surfaced to debug text, never silently swallowed.
  - **Suppression is speech-only, never frame-dropping.** Every frame past the
    `minFrameInterval` throttle is sent and the on-screen summary/spatial/risk
    always reflects the latest frame. We do **not** try to guess "the scene didn't
    change" and drop frames on-device — for a vision-assistance app that would
    hide real changes (e.g. panning the camera to new content) from the user,
    which is unsafe. Avoiding repetition happens at the *speech* layer via
    `SpeechGate` so the screen never goes stale.
- Ask a specific question (single-turn, **voice only** — there is no text input;
  the app is designed for hands-free / low-vision use):
  - Hold the `按住说话` button and speak (press-to-talk). Speech is transcribed
    on-device where supported (`zh-CN`), falling back to Apple's server
    recognition otherwise.
  - The recognized text is sent with the next frame; the model is told to answer
    it directly (mode template still provides the base prompt).
  - A voice question is **single-turn**: it is answered once and then cleared, so
    it does not stick to every subsequent frame. Multi-turn voice conversation is
    planned for a later phase.
  - Holding to talk mutes any ongoing spoken output and temporarily switches the
    audio session to record; it is restored to playback afterwards.
  - Requires the microphone and speech-recognition permissions (prompted on first
    use / on `开始视觉辅助`).
- Model output is now visual-assistance structured, not a thin scene label: the
  backend requests `summary`, `spatial_description`, `risk_level`,
  `risk_message`, `suggested_action`, `spoken_text`, OCR text, and continuity
  fields directly from the VLM via JSON Schema. `fusion.py` remains the safety
  fallback, not the primary source of direction/risk intelligence.
- When a previous frame exists, the client sends it along with the current frame
  so the model can compare visual changes directly, not only through text
  context. The prompt explicitly says the current frame is the source of truth.
- `读文字`/detail/question flows run Apple Vision OCR on-device and attach the
  OCR text to the frame request as a model hint.

## iOS Workflow

1. Initialize iOS project once in Xcode (required):
   - Create `VQASee.xcodeproj` in `ios-vqa-app/VQASee`
   - Configure signing/team/bundle ID
   - Enable capabilities/permissions (camera/location/local-network)
   - Install iOS platform runtime in `Xcode > Settings > Components` (or run `xcodebuild -downloadPlatform iOS`)
2. Copy export template:
   - `cp deploy/ios/ExportOptions.plist.template deploy/ios/ExportOptions.plist`
3. Run automation:
   - Preflight: `bash deploy/ios/preflight.sh`
   - Build (device/release signing required): `bash deploy/ios/build.sh`
   - Build (simulator, no signing): `SDK=iphonesimulator CONFIGURATION=Debug bash deploy/ios/build.sh`
   - Test: `bash deploy/ios/test.sh`
   - Install to connected iPhone (Debug): `bash deploy/ios/install_on_device.sh`
   - Install to specific iPhone: `DEVICE_ID=<iphone_udid> bash deploy/ios/install_on_device.sh`
   - Archive + export: `bash deploy/ios/archive.sh`
   - Upload TestFlight: `bash deploy/ios/release_testflight.sh`
