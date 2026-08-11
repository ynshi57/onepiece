"""HTTP API for local diagnostic capture management."""

from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.diagnostic_capture import capture_root, get_session_dir, list_sessions


router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


class DiagnosticLabel(BaseModel):
    frame: str
    label: str
    note: str = ""


def _load_labels(session_dir: Path) -> list[dict]:
    labels_path = session_dir / "labels.jsonl"
    if not labels_path.is_file():
        return []
    labels = []
    for line in labels_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            labels.append(value)
    return labels


def _html_page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif; margin: 24px; background: #0b0f14; color: #f5f5f7; }}
    a {{ color: #64d2ff; }}
    .card {{ background: #1c1c1e; border: 1px solid #3a3a3c; border-radius: 16px; padding: 16px; margin: 16px 0; }}
    img {{ max-width: 420px; border-radius: 12px; border: 1px solid #3a3a3c; }}
    input, select, textarea, button {{ font: inherit; margin: 4px; }}
    input, select, textarea {{ background: #2c2c2e; color: #fff; border: 1px solid #555; border-radius: 8px; padding: 8px; }}
    button {{ background: #0a84ff; color: #fff; border: 0; border-radius: 8px; padding: 8px 12px; }}
    .muted {{ color: #a1a1a6; }}
    .row {{ display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap; }}
    pre {{ white-space: pre-wrap; background: #111; padding: 12px; border-radius: 12px; max-width: 680px; }}
  </style>
</head>
<body>
{body}
</body>
</html>"""
    )


@router.get("/sessions")
def sessions() -> dict:
    return {"sessions": list_sessions()}


@router.get("/sessions/{session_id}")
def session_detail(session_id: str) -> dict:
    session_dir = get_session_dir(session_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    metadata_path = session_dir / "metadata.json"
    manifest_path = session_dir / "manifest.jsonl"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    frames = sorted(str(path.relative_to(session_dir)) for path in (session_dir / "frames").glob("*.jpg")) if (session_dir / "frames").is_dir() else []
    manifest_rows = 0
    if manifest_path.is_file():
        manifest_rows = sum(1 for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {
        "session_id": session_id,
        "metadata": metadata,
        "frame_count": len(frames),
        "manifest_rows": manifest_rows,
        "frames": frames[:200],
        "labels": _load_labels(session_dir),
    }


@router.get("/sessions/{session_id}/frames/{frame_name}")
def session_frame(session_id: str, frame_name: str):
    session_dir = get_session_dir(session_id)
    frame_path = (session_dir / "frames" / Path(frame_name).name).resolve()
    frames_dir = (session_dir / "frames").resolve()
    if frames_dir not in frame_path.parents or not frame_path.is_file():
        raise HTTPException(status_code=404, detail="frame_not_found")
    return FileResponse(frame_path, media_type="image/jpeg")


@router.post("/sessions/{session_id}/labels")
def add_label(session_id: str, label: DiagnosticLabel) -> dict:
    session_dir = get_session_dir(session_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    labels_path = session_dir / "labels.jsonl"
    with labels_path.open("a", encoding="utf-8") as handle:
        handle.write(label.model_dump_json())
        handle.write("\n")
    return {"status": "ok"}


@router.get("/ui", response_class=HTMLResponse)
def diagnostics_ui():
    cards = []
    for item in list_sessions():
        sid = html.escape(item["session_id"])
        cards.append(
            f"<div class='card'><h2>{sid}</h2>"
            f"<p class='muted'>frames: {item['frame_count']} · manifest rows: {item['manifest_rows']}</p>"
            f"<p><a href='/diagnostics/sessions/{sid}/annotate'>打开标注</a> · "
            f"<button onclick=\"deleteSession('{sid}')\">删除 session</button></p></div>"
        )
    script = """<script>
async function deleteSession(sessionId) {
  if (!confirm('确定删除这个诊断 session 吗？')) return;
  const resp = await fetch('/diagnostics/sessions/' + sessionId, {method: 'DELETE'});
  if (resp.ok) location.reload(); else alert('删除失败');
}
</script>"""
    body = "<h1>VQASee 诊断标注台</h1><p class='muted'>本页面只服务本地 Mac 后端诊断数据。删除不可恢复。</p>" + script + ("".join(cards) or "<p>暂无 session。</p>")
    return _html_page("VQASee 诊断标注台", body)


@router.get("/sessions/{session_id}/annotate", response_class=HTMLResponse)
def annotate_session(session_id: str):
    session_dir = get_session_dir(session_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    detail = session_detail(session_id)
    labels = _load_labels(session_dir)
    labels_by_frame: dict[str, list[dict]] = {}
    for label in labels:
        labels_by_frame.setdefault(str(label.get("frame", "")), []).append(label)

    rows = []
    for frame in detail["frames"]:
        frame_name = Path(frame).name
        safe_frame = html.escape(frame_name)
        existing = labels_by_frame.get(frame, []) + labels_by_frame.get(frame_name, [])
        existing_html = ""
        if existing:
            existing_html = "<pre>" + html.escape(json.dumps(existing, ensure_ascii=False, indent=2)) + "</pre>"
        rows.append(
            f"""<div class='card'>
  <h2>{safe_frame}</h2>
  <div class='row'>
    <img src='/diagnostics/sessions/{html.escape(session_id)}/frames/{safe_frame}' alt='{safe_frame}'>
    <div>
      <label>标注类型</label><br>
      <select id='label-{safe_frame}'>
        <option value='correct'>正确</option>
        <option value='false_positive'>误检</option>
        <option value='missed'>漏检</option>
        <option value='wrong_class'>类别错误</option>
        <option value='bad_box'>框不准</option>
        <option value='stale_result'>旧结果</option>
        <option value='other'>其他</option>
      </select><br>
      <textarea id='note-{safe_frame}' rows='4' cols='42' placeholder='例如：水桶被识别成车辆；左侧漏检自行车'></textarea><br>
      <button onclick="submitLabel('{safe_frame}')">保存标注</button>
      <div id='status-{safe_frame}' class='muted'></div>
      <h3>已有标注</h3>
      {existing_html}
    </div>
  </div>
</div>"""
        )
    script = f"""<script>
async function submitLabel(frame) {{
  const label = document.getElementById('label-' + frame).value;
  const note = document.getElementById('note-' + frame).value;
  const resp = await fetch('/diagnostics/sessions/{html.escape(session_id)}/labels', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{frame: 'frames/' + frame, label, note}})
  }});
  document.getElementById('status-' + frame).textContent = resp.ok ? '已保存，刷新页面可查看。' : '保存失败';
}}
</script>"""
    body = f"<p><a href='/diagnostics/ui'>← 返回 sessions</a></p><h1>标注 session: {html.escape(session_id)}</h1>" + script + "".join(rows)
    return _html_page(f"标注 {session_id}", body)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    session_dir = get_session_dir(session_id).resolve()
    root = capture_root().resolve()
    if root not in session_dir.parents or not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    shutil.rmtree(session_dir)
    return {"status": "deleted", "session_id": session_id}


@router.post("/cleanup")
def cleanup_sessions(older_than_days: int = Query(7, ge=1, le=365)) -> dict:
    root = capture_root().resolve()
    if not root.is_dir():
        return {"deleted": [], "older_than_days": older_than_days}
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    deleted: list[str] = []
    for path in root.glob("session-*"):
        if not path.is_dir():
            continue
        metadata_path = path / "metadata.json"
        created_at: datetime | None = None
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                raw_created = metadata.get("created_at")
                if isinstance(raw_created, str):
                    created_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                created_at = None
        if created_at is None:
            created_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if created_at < cutoff:
            shutil.rmtree(path)
            deleted.append(path.name.removeprefix("session-"))
    return {"deleted": deleted, "older_than_days": older_than_days}


@router.get("/root")
def diagnostics_root() -> dict:
    return {"root": str(capture_root())}
