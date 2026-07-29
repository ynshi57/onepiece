# iOS App Initialization Guide

This directory keeps the Xcode project and fastlane settings.

## One-time Xcode setup

1. Open Xcode and create a new iOS App project.
2. Set:
   - Product Name: `VQASee`
   - Interface: `SwiftUI`
   - Language: `Swift`
3. Save project into this directory so project file is:
   - `ios-vqa-app/VQASee/VQASee.xcodeproj`
4. Configure signing:
   - Select your Team
   - Set Bundle Identifier
5. Privacy usage descriptions are localized via `VQASee/InfoPlist.xcstrings`
   (camera / microphone / local network / location / speech recognition), in
   both `zh-Hans` and `en`. Do NOT re-add `INFOPLIST_KEY_NS*UsageDescription`
   build settings — those hardcoded values override the catalog and would break
   localization of the permission prompts.
6. Install iOS runtime component if prompted:
   - Xcode `Settings > Components`
   - or `xcodebuild -downloadPlatform iOS`

## Install to real iPhone

From repo root:

- `bash deploy/ios/install_on_device.sh`
- Or choose a specific device:
  - `DEVICE_ID=<iphone_udid> bash deploy/ios/install_on_device.sh`

This script builds Debug for `iphoneos`, installs with `xcrun devicectl`, then launches the app.

## Real-device networking reminder

- On iPhone, do not use `localhost` for backend URL.
- Use Mac LAN URL such as `ws://192.168.1.10:9000/ws/signaling`.
- Ensure Mac and iPhone are on the same Wi-Fi and `VQASee` is allowed in iOS local network privacy settings.
- If iPhone uses cellular and Mac is on another network, direct LAN access will timeout; use iPhone hotspot or a public tunnel URL (`wss://...`).

## Nearby auto-connect mode

Recommended for personal use:

1. Enable iPhone Personal Hotspot.
2. Connect the Mac to the iPhone hotspot.
3. Run backend on Mac:
   - `bash ../start_backend.sh`
4. Launch VQASee and tap `开始视觉辅助`.

The app discovers the Mac backend via Bonjour service `_vqasee._tcp`, so the main
screen does not require entering a server URL, relay token, worker ID, or client
ID. If Bonjour does not resolve, it also probes common iPhone hotspot addresses
such as `172.20.10.x`. Manual fields remain under `高级设置` for debugging.

## Cross-network relay mode

When the iPhone is on 4G/5G and the Mac worker is on Wi-Fi, use the relay
endpoint instead of the direct LAN endpoint:

- Server URL: `wss://<relay-host>/ws/client`
- Pairing token: same `RELAY_PAIRING_TOKEN` used by relay and worker
- Worker ID: same `WORKER_ID` used by the Mac worker
- Client ID: any stable device name

The app treats `/ws/client` URLs as relay-mode connections. In relay mode it
sends `client_register` first, then sends camera frames as `frame_request`.

Current traffic limits in the app:

- Mode-aware JPEG limits:
  - `行走`: `448px`, quality `0.45`, max `120KB`
  - `周围`: `640px`, quality `0.55`, max `220KB`
  - `详细`: `768px`, quality `0.62`, max `320KB`
  - `读文字`: `1024px`, quality `0.72`, max `520KB`
- Minimum continuous-frame interval: `2s`
- Maximum in-flight frame requests: `1`

## Visual-assistance UI (immersive, single screen)

The main screen is an immersive, full-screen camera experience designed for
blind / low-vision users — no vertical scrolling of the page itself:

- The camera preview is the full-screen hero (`.ignoresSafeArea()`).
- A floating status pill (connection state) and a settings gear sit at the top
  as translucent glass overlays.
- A bottom control cluster floats over the camera via `safeAreaInset(.bottom)`:
  the answer panel (summary / spatial direction / risk / suggested action +
  latency), the mode bar, the press-to-talk button, and a single start/stop
  control.
- The **only** thing that ever scrolls is the answer text inside the answer
  panel, capped at ~35% of screen height so the fixed controls are always
  reachable at any Dynamic Type size.
- Modes: `周围` / `行走` / `读文字` / `详细`.
- Press-and-hold "按住说话" for a voice question (answered first, then cleared).
- Everything else — voice-broadcast toggle, model selector, multi-backend
  picker, advanced server fields, debug text, hotspot help — lives in the
  **Settings** sheet (the gear), the only surface with text input / keyboard.

Model selector (in Settings):

- `自动` chooses model by mode (`行走` → 3B, other high-detail modes → 7B)
- `快速 3B` sends `qwen2.5vl:3b`
- `更准 7B` sends `qwen2.5vl:7b`

Speech uses the built-in iOS `AVSpeechSynthesizer` (TTS) and `SFSpeechRecognizer`
(voice questions). No extra voice library is needed.

`读文字` and detail/question flows also run Apple Vision OCR on-device and send
that text to the backend as a hint, so text reading does not rely solely on the
VLM seeing small characters in the compressed frame.

Before using `更准 7B`, pull the model on the Mac:

```bash
MODEL=qwen2.5vl:7b bash ../start_qwen_local.sh
```

## Source layout (view layer split)

`ContentView.swift` used to be a single ~2100-line file. It is now split by
concern; all files live under `VQASee/VQASee/` and are auto-included via the
project's `PBXFileSystemSynchronizedRootGroup` (no `project.pbxproj` edits
needed to add a Swift file).

Logic (behavior unchanged — pure relocation):

- `Models.swift` — value types & UI-facing enums (`StreamStatus`,
  `AssistanceMode`, `VqaModelOption`, `VqaDisplayResult`, …)
- `PureHelpers.swift` — pure, unit-tested helpers (Foundation-only)
- `Networking.swift` — signaling / transport + JPEG encoder
- `BonjourDiscovery.swift` — `NearbyServerBrowser`
- `CameraCapture.swift` — frame proxy + camera preview
- `StreamingViewModel.swift` — the core `ObservableObject`
- `SpeechRecognitionController.swift` — press-to-talk speech input

View:

- `ContentView.swift` — thin root (owns the view model, presents the screen +
  settings sheet)
- `AssistanceScreen.swift` — the immersive single-screen layout
- `SettingsView.swift` — the settings sheet
- Components: `GlassPanel`, `StatusPill`, `AnswerPanel`, `ModeBar`,
  `PressToTalkButton`, `ServerPickerView`
- `Theme.swift` — the design system

The pure-logic types (`StreamingConfigValidator`, `AutoConnectPolicy`,
`SockaddrParser`, `SignalingResponseParser`, `FrameMessageBuilder`,
`LatencyBreakdown`, `SpeechGate`, `FrameContext`, …) keep their names,
signatures and visibility so `VQASeeTests` compiles and passes unchanged.

## Design system (`Theme.swift`)

A single `enum Theme` namespace keeps the UI coherent:

- `Spacing` (4 / 8 / 12 / 16 / 24) and `Radius` (panel 20, pill 999).
- `Typography` built on Dynamic Type text styles (never fixed point sizes), so
  everything scales with the user's preferred content size.
- Semantic colors read from the asset catalog (`AccentColor`, `RiskWarning`,
  `RiskDanger`), so they adapt to dark mode and Increase Contrast automatically;
  `Theme.riskColor(for:)` maps a backend risk level to its color.
- `GlassPanel` is the single Liquid-Glass-vs-solid-surface decision point: it
  uses the translucent material normally and falls back to an opaque surface
  when Reduce Transparency is on.

## Localization (中文 + English)

The UI follows the system language (Simplified Chinese or English):

- Strings live in `VQASee/Localizable.xcstrings` (source language `zh-Hans`,
  English column filled). `Text("中文")` literals auto-extract on build; enum
  titles/hints route through `String(localized:)`.
- Privacy prompts live in `VQASee/InfoPlist.xcstrings`.
- Not localized by design: `AssistanceMode.prompt` (model-steering instructions
  — must stay Chinese or model quality degrades) and `VqaModelOption` raw values
  (model IDs).
- **Adding a language** = add it to `knownRegions` in `project.pbxproj` and fill
  its column in both `.xcstrings` catalogs. No code change.

## Accessibility

- Dynamic Type throughout (relative fonts, no fixed heights on text).
- VoiceOver: the answer panel is one combined element read risk-first (safety
  before summary / direction / advice); the status pill is one
  `.updatesFrequently` element; the raw camera preview is hidden from VoiceOver.
- Large tap targets (`.controlSize(.large)`) for start/stop and press-to-talk.
- High contrast via the `RiskWarning` / `RiskDanger` colorsets rather than raw
  `.orange` / `.red`.
- Reduce Transparency falls back to opaque surfaces (see `GlassPanel`).

## fastlane setup

0. Ensure Ruby >= 3.2 (`ruby --version`)
1. Copy `.env.default` to `.env` and fill real values.
2. Run:
   - `bundle install`
3. Validate lane:
   - `bundle exec fastlane lanes`
