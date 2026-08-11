"""Persist iPhone diagnostic frames uploaded over the signaling WebSocket.

Diagnostic capture is explicitly user-enabled on iOS. The backend stores images
and metadata in a local folder so model failures can be inspected offline.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SAFE_CHARS = re.compile(r"[^a-zA-Z0-9_.-]+")


def capture_root() -> Path:
    configured = os.getenv("DIAGNOSTIC_CAPTURE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "diagnostic-captures"


def _safe_name(value: str, fallback: str) -> str:
    cleaned = _SAFE_CHARS.sub("-", value.strip())[:80].strip(".-")
    return cleaned or fallback


def _next_frame_name(frames_dir: Path) -> str:
    existing = list(frames_dir.glob("frame-*.jpg"))
    return f"frame-{len(existing) + 1:04d}.jpg"


def get_session_dir(session_id: str) -> Path:
    return capture_root() / f"session-{_safe_name(session_id, 'unknown-session')}"


def list_sessions() -> list[dict]:
    root = capture_root()
    if not root.is_dir():
        return []
    sessions = []
    for path in sorted(root.glob("session-*"), reverse=True):
        if not path.is_dir():
            continue
        manifest = path / "manifest.jsonl"
        frames_dir = path / "frames"
        sessions.append({
            "session_id": path.name.removeprefix("session-"),
            "path": str(path),
            "frame_count": len(list(frames_dir.glob("*.jpg"))) if frames_dir.is_dir() else 0,
            "manifest_rows": sum(1 for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()) if manifest.is_file() else 0,
        })
    return sessions


def save_diagnostic_frame(session_id: str, image_base64: str, metadata: dict[str, Any]) -> dict:
    if not isinstance(image_base64, str) or not image_base64:
        raise ValueError("invalid_image_base64")
    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid_image_base64") from exc

    diagnostic_session_id = str(metadata.get("diagnostic_session_id") or session_id)
    session_dir = get_session_dir(diagnostic_session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = session_dir / "metadata.json"
    if not metadata_path.exists():
        metadata_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "format": "vqasee-backend-diagnostic-v1",
                    "source": "iOS diagnostic_frame websocket upload",
                    "privacy": "user-enabled diagnostic capture stored on local backend",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    frames_dir = session_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    frame_name = _next_frame_name(frames_dir)
    frame_path = frames_dir / frame_name
    frame_path.write_bytes(image_bytes)

    record = dict(metadata) if isinstance(metadata, dict) else {}
    record["frame"] = f"frames/{frame_name}"
    record["backend_saved_frame"] = f"frames/{frame_name}"
    record["backend_saved_at"] = datetime.now(timezone.utc).isoformat()

    manifest_path = session_dir / "manifest.jsonl"
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")

    return {
        "session_id": session_dir.name.removeprefix("session-"),
        "session_dir": str(session_dir),
        "frame": f"frames/{frame_name}",
        "bytes": len(image_bytes),
    }
