# VQASee Relay Server

Public WebSocket relay for cross-network VQA streaming.

It lets an iPhone on 4G/5G and a local Mac VQA worker on Wi-Fi communicate without
LAN routing, port forwarding, or exposing the Mac backend directly.

## Start

```bash
export RELAY_PAIRING_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
bash ../start_relay.sh
```

Endpoints:

- `GET /health`
- `WS /ws/worker` for local VQA workers
- `WS /ws/client` for iPhone clients

## MVP auth

This relay intentionally avoids full login for now, but it still requires a shared
`RELAY_PAIRING_TOKEN` for both worker and client registration.

## Traffic limits

Default limits:

- `MAX_FRAME_BASE64_BYTES=300000`
- `MAX_FRAMES_PER_MINUTE=30`
- `MAX_INFLIGHT_PER_CLIENT=1`
- `RELAY_REQUEST_TIMEOUT_SECONDS=30`

