# Local VQA Backend

## Install

```bash
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -r requirements-dev.txt
```

## Run tests

```bash
source ../.venv/bin/activate
pytest tests
```

## Start API

```bash
source ../.venv/bin/activate
uvicorn app.main:app --reload --port 9000
```

For iPhone hotspot / nearby auto-connect, prefer the root script because it also
exports the advertised service port:

```bash
bash ../start_backend.sh
```

The backend advertises a Bonjour service:

- service type: `_vqasee._tcp`
- port: backend port, default `9000`
- path: `/ws/signaling`

Set `VQASEE_DISABLE_BONJOUR=1` to disable local discovery.

### Optional Qwen-VL inference configuration

If you already have a Qwen-compatible OpenAI API endpoint, set:

```bash
export QWEN_API_BASE_URL="http://127.0.0.1:8000"
export QWEN_MODEL="Qwen/Qwen2.5-VL-3B-Instruct"
export QWEN_TIMEOUT_SECONDS="45"
```

Without these vars, backend uses the local heuristic fallback path.

### Local free setup via Ollama (recommended for <=16GB)

```bash
# auto-install (if possible), auto-start service, then pull model
bash ../start_qwen_local.sh

export QWEN_API_BASE_URL="http://127.0.0.1:11434"
export QWEN_MODEL="qwen2.5vl:3b"

# one command for Qwen + backend
bash ../start_local_vqa.sh
```

To try the more accurate but slower 7B model:

```bash
MODEL=qwen2.5vl:7b bash ../start_qwen_local.sh
QWEN_API_BASE_URL="http://127.0.0.1:11434" QWEN_MODEL="qwen2.5vl:7b" bash ../start_backend.sh
```

The iOS app can also send a per-frame `model` override (`qwen2.5vl:3b` or
`qwen2.5vl:7b`) when the backend is using Ollama.

## API endpoints

- `GET /health`
- `POST /v1/vqa`
- `WS /ws/signaling`

## Cross-network worker mode

For iPhone-on-4G and Mac-on-Wi-Fi use cases, run the public relay first, then run
this backend as an outbound worker:

```bash
export RELAY_WORKER_URL="ws://<relay-host>:9100/ws/worker"
export RELAY_PAIRING_TOKEN="<same-token-as-relay-and-ios>"
export WORKER_ID="local-mac-worker"
bash ../start_worker.sh
```

The worker receives `inference_request` messages from the relay, runs local VQA,
and sends `inference_result` back through the relay. It enforces:

- `MAX_FRAME_BASE64_BYTES` default `300000`
- `WORKER_INFERENCE_TIMEOUT_SECONDS` default `20`
- `WORKER_DROP_IF_BUSY` default `1`

## WebSocket signaling quick test

The iOS app defaults to `ws://localhost:9000/ws/signaling`.
After startup, backend sends:

- `{"type":"server_ready","session_id":"..."}`

When client sends `stream_start`, backend replies:

- `{"type":"stream_ack","frame_id":"..."}`

Client can then push camera frames:

- `{"type":"frame","frame_id":"...","prompt":"...","image_base64":"...","gps":{"lat":...,"lon":...}}`
