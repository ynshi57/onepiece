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
from app.diagnostic_report import generate_report_from_session_dir


router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


class DiagnosticLabel(BaseModel):
    frame: str
    label: str
    note: str = ""
    true_scene: str = ""
    true_risks: str = ""
    false_positives: str = ""
    missed_risks: str = ""


def _load_labels(session_dir: Path) -> list[dict]:
    labels_path = session_dir / "labels.jsonl"
    if not labels_path.is_file():
        return []
    labels = []
    for line_index, line in enumerate(labels_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            value = dict(value)
            value["_index"] = line_index
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
    .hint {{ color: #d1d1d6; max-width: 720px; line-height: 1.45; }}
    .pill {{ display: inline-block; background: #2c2c2e; color: #d1d1d6; padding: 3px 8px; border-radius: 999px; margin: 2px; }}
    .danger {{ background: #ff453a; }}
    .label-item {{ background: #111; padding: 10px; border-radius: 12px; margin: 8px 0; }}
    .field-grid {{ display: grid; grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr); gap: 8px; max-width: 760px; }}
    .field-grid label {{ color: #d1d1d6; font-size: 0.92rem; }}
    .field-grid textarea {{ width: 100%; box-sizing: border-box; }}
    .explain {{ color: #8e8e93; font-size: 0.92rem; margin-top: 4px; }}
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


@router.delete("/sessions/{session_id}/labels/{label_index}")
def delete_label(session_id: str, label_index: int) -> dict:
    session_dir = get_session_dir(session_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    labels_path = session_dir / "labels.jsonl"
    if not labels_path.is_file():
        raise HTTPException(status_code=404, detail="label_not_found")
    lines = labels_path.read_text(encoding="utf-8").splitlines()
    if label_index < 0 or label_index >= len(lines) or not lines[label_index].strip():
        raise HTTPException(status_code=404, detail="label_not_found")
    kept = [line for index, line in enumerate(lines) if index != label_index]
    labels_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return {"status": "deleted", "label_index": label_index}


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


@router.get("/sessions/{session_id}/report")
def session_report(session_id: str) -> dict:
    session_dir = get_session_dir(session_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    return generate_report_from_session_dir(session_id=session_id, session_dir=session_dir)


@router.get("/sessions/{session_id}/report/ui", response_class=HTMLResponse)
def session_report_ui(session_id: str):
    report = session_report(session_id)
    metrics = report.get("metrics", {}) if isinstance(report.get("metrics"), dict) else {}
    findings = report.get("findings", []) if isinstance(report.get("findings"), list) else []
    tasks = report.get("task_suggestions", []) if isinstance(report.get("task_suggestions"), list) else []

    metric_html = "".join(
        f"<span class='pill'>{html.escape(str(key))}: {html.escape(str(value))}</span>"
        for key, value in metrics.items()
        if key not in {"local_objects", "labels"}
    )
    object_html = "<pre>" + html.escape(json.dumps(metrics.get("local_objects", {}), ensure_ascii=False, indent=2)) + "</pre>"
    label_html = "<pre>" + html.escape(json.dumps(metrics.get("labels", {}), ensure_ascii=False, indent=2)) + "</pre>"

    finding_html = ""
    for finding in findings:
        finding_html += (
            "<div class='label-item'>"
            f"<p><b>{html.escape(str(finding.get('severity', ''))).upper()} · {html.escape(str(finding.get('title', '')))}</b></p>"
            f"<p>负责人：{html.escape(str(finding.get('owner', '')))}</p>"
            f"<p>证据：{html.escape(str(finding.get('evidence', '')))}</p>"
            f"<p>建议：{html.escape(str(finding.get('recommendation', '')))}</p>"
            "</div>"
        )
    if not finding_html:
        finding_html = "<p class='muted'>暂无明确问题。请先标注误报/漏报帧。</p>"

    task_html = ""
    for task in tasks:
        task_html += (
            "<div class='label-item'>"
            f"<p><b>{html.escape(str(task.get('title', '')))}</b></p>"
            f"<p>主责：{html.escape(str(task.get('primary', '')))}</p>"
            f"<p>验收：{html.escape(str(task.get('acceptance', '')))}</p>"
            "</div>"
        )
    if not task_html:
        task_html = "<p class='muted'>暂无任务建议。</p>"

    body = f"""
<p><a href='/diagnostics/ui'>← 返回 sessions</a> · <a href='/diagnostics/sessions/{html.escape(session_id)}/annotate'>打开标注</a></p>
<h1>评估报告：{html.escape(session_id)}</h1>
<p class='hint'>这份报告给乔布斯/罗根/思余/全麦看，用于发现产品、系统、UI 和模型问题，不给普通用户看。</p>
<div class='card'><h2>核心结论</h2><p>{html.escape(str(report.get('headline', '')))}</p></div>
<div class='card'><h2>关键指标</h2>{metric_html}<h3>本地检测对象</h3>{object_html}<h3>人工标注</h3>{label_html}</div>
<div class='card'><h2>自动发现的问题</h2>{finding_html}</div>
<div class='card'><h2>建议任务卡</h2>{task_html}</div>
"""
    return _html_page(f"评估报告 {session_id}", body)


@router.get("/sessions/{session_id}/annotate", response_class=HTMLResponse)
def annotate_session(session_id: str):
    session_dir = get_session_dir(session_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    detail = session_detail(session_id)
    labels = _load_labels(session_dir)
    manifest_by_frame: dict[str, dict] = {}
    manifest_path = session_dir / "manifest.jsonl"
    if manifest_path.is_file():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                frame_key = str(row.get("backend_saved_frame") or row.get("frame") or "")
                if frame_key:
                    manifest_by_frame[frame_key] = row
                    manifest_by_frame[Path(frame_key).name] = row
    labels_by_frame: dict[str, list[dict]] = {}
    for label in labels:
        labels_by_frame.setdefault(str(label.get("frame", "")), []).append(label)

    rows = []
    for frame in detail["frames"]:
        frame_name = Path(frame).name
        safe_frame = html.escape(frame_name)
        existing = labels_by_frame.get(frame, []) + labels_by_frame.get(frame_name, [])
        existing_html = "<p class='muted'>暂无标注。请选择上方类型，并在备注里写真实情况或错误原因。</p>"
        if existing:
            parts = []
            for label in existing:
                label_index = label.get("_index")
                label_text = html.escape(str(label.get("label", "")))
                note_text = html.escape(str(label.get("note", "")))
                frame_text = html.escape(str(label.get("frame", "")))
                true_scene = html.escape(str(label.get("true_scene", "")))
                true_risks = html.escape(str(label.get("true_risks", "")))
                false_positives = html.escape(str(label.get("false_positives", "")))
                missed_risks = html.escape(str(label.get("missed_risks", "")))
                delete_button = ""
                if isinstance(label_index, int):
                    delete_button = f'<button class="danger" onclick="deleteLabel({label_index})">删除这条标注</button>'
                detail_lines = []
                if true_scene:
                    detail_lines.append(f"<p><b>真实画面：</b>{true_scene}</p>")
                if true_risks:
                    detail_lines.append(f"<p><b>真实风险：</b>{true_risks}</p>")
                if false_positives:
                    detail_lines.append(f"<p><b>误报内容：</b>{false_positives}</p>")
                if missed_risks:
                    detail_lines.append(f"<p><b>漏报内容：</b>{missed_risks}</p>")
                if note_text:
                    detail_lines.append(f"<p><b>备注：</b>{note_text}</p>")
                parts.append(
                    f"<div class='label-item'><p><b>{label_text}</b> · <span class='muted'>{frame_text}</span></p>"
                    f"{''.join(detail_lines) or '<p>无备注</p>'}{delete_button}</div>"
                )
            existing_html = "".join(parts)
        manifest = manifest_by_frame.get(frame, manifest_by_frame.get(frame_name, {}))
        mode = html.escape(str(manifest.get("mode", "unknown"))) if isinstance(manifest, dict) else "unknown"
        event = html.escape(str(manifest.get("event", "unknown"))) if isinstance(manifest, dict) else "unknown"
        reason = html.escape(str(manifest.get("reason", ""))) if isinstance(manifest, dict) else ""
        local_context = ""
        perception_context = ""
        if isinstance(manifest, dict):
            local_vision = manifest.get("local_vision") if isinstance(manifest.get("local_vision"), dict) else {}
            perception = manifest.get("perception") if isinstance(manifest.get("perception"), dict) else {}
            local_context = html.escape(str(local_vision.get("backend_context", "")))
            perception_context = html.escape(str(perception.get("backend_context", "")))
        rows.append(
            f"""<div class='card'>
  <h2>{safe_frame}</h2>
  <p><span class='pill'>mode: {mode}</span><span class='pill'>event: {event}</span><span class='pill'>{reason}</span></p>
  <div class='row'>
    <img src='/diagnostics/sessions/{html.escape(session_id)}/frames/{safe_frame}' alt='{safe_frame}'>
    <div>
      <p class='hint'>画面变化检测：{local_context or '未见明显变化'}<br>目标检测结果：{perception_context or '无目标'}</p>
      <p class='explain'>说明：“画面变化明显”只表示亮度/纹理变化，不代表识别到物体；“目标检测结果：无”表示本地 YOLO 没检测到人/车/障碍等目标。</p>
      <label>标注类型</label><br>
      <select id='label-{safe_frame}'>
        <option value='scene_truth'>真实画面记录：只记录我看到了什么</option>
        <option value='no_obvious_risk'>无明显风险</option>
        <option value='false_positive'>误报：提示有风险/物体，但实际没有</option>
        <option value='wrong_class'>类别错误：例如水桶识别成车辆</option>
        <option value='missed_risk'>漏报：真实有风险但没提示</option>
        <option value='wrong_direction'>方向/位置错误</option>
        <option value='output_error'>模型输出异常/不可用</option>
        <option value='stale_or_inflight'>旧结果/后端处理中</option>
        <option value='image_quality_issue'>图像质量/方向问题</option>
        <option value='other'>其他</option>
      </select><br>
      <div class='field-grid'>
        <label>真实画面<textarea id='true-scene-{safe_frame}' rows='3' placeholder='例如：室内走廊，浅色地板，右前方有几个蓝色水桶'></textarea></label>
        <label>真实风险<textarea id='true-risks-{safe_frame}' rows='3' placeholder='例如：无明显风险；右侧水桶靠近通行边缘；前方有台阶'></textarea></label>
        <label>误报内容<textarea id='false-positives-{safe_frame}' rows='3' placeholder='例如：把水桶误报成车辆；把鞋尖误报成人'></textarea></label>
        <label>漏报内容<textarea id='missed-risks-{safe_frame}' rows='3' placeholder='例如：漏报右侧水桶；漏报前方台阶'></textarea></label>
      </div>
      <textarea id='note-{safe_frame}' rows='3' cols='56' placeholder='补充说明，可不填'></textarea><br>
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
  const true_scene = document.getElementById('true-scene-' + frame).value;
  const true_risks = document.getElementById('true-risks-' + frame).value;
  const false_positives = document.getElementById('false-positives-' + frame).value;
  const missed_risks = document.getElementById('missed-risks-' + frame).value;
  const resp = await fetch('/diagnostics/sessions/{html.escape(session_id)}/labels', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{frame: 'frames/' + frame, label, note, true_scene, true_risks, false_positives, missed_risks}})
  }});
  document.getElementById('status-' + frame).textContent = resp.ok ? '已保存，刷新页面可查看。' : '保存失败';
}}
async function deleteLabel(labelIndex) {{
  if (!confirm('删除这条标注吗？')) return;
  const resp = await fetch('/diagnostics/sessions/{html.escape(session_id)}/labels/' + labelIndex, {{method: 'DELETE'}});
  if (resp.ok) location.reload(); else alert('删除失败');
}}
</script>"""
    help_text = """<p class='hint'>怎么标：优先填写“真实画面”和“真实风险”。如果系统把不存在的东西说出来，再填写“误报内容”；如果真实有危险但系统没提示，填写“漏报内容”。这些字段会变成可统计的 ground truth，比单纯备注更有用。</p>"""
    body = f"<p><a href='/diagnostics/ui'>← 返回 sessions</a> · <a href='/diagnostics/sessions/{html.escape(session_id)}/report/ui'>查看评估报告</a></p><h1>标注 session: {html.escape(session_id)}</h1>" + help_text + script + "".join(rows)
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
