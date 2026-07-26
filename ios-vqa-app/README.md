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
5. Enable capabilities:
   - Camera usage description in `Info.plist`
   - Location usage description in `Info.plist`
   - Local network usage description in `Info.plist` (for connecting to Mac LAN backend)
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

- Maximum JPEG dimension: `448px`
- JPEG quality target: `0.45`
- Maximum JPEG bytes before Base64: `120KB`
- Minimum continuous-frame interval: `2s`
- Maximum in-flight frame requests: `1`

## Visual-assistance UI and speech

The main screen is designed for visual assistance rather than debugging:

- Large natural-language summary
- Risk message and suggested action
- Mode buttons: `周围`, `行走`, `读文字`, `详细`
- Voice toggle using the built-in iOS `AVSpeechSynthesizer`
- `测试语音` button for checking iPhone audio output
- Model selector:
  - `快速 3B` sends `qwen2.5vl:3b`
  - `更准 7B` sends `qwen2.5vl:7b`
- Debug fields remain under `高级设置`

No extra voice library is needed.

Before using `更准 7B`, pull the model on the Mac:

```bash
MODEL=qwen2.5vl:7b bash ../start_qwen_local.sh
```

## fastlane setup

0. Ensure Ruby >= 3.2 (`ruby --version`)
1. Copy `.env.default` to `.env` and fill real values.
2. Run:
   - `bundle install`
3. Validate lane:
   - `bundle exec fastlane lanes`
