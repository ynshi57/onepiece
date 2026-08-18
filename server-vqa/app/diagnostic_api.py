"""HTTP API for local diagnostic capture management."""

from __future__ import annotations

import html
import json
import os
import shutil
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from PIL import Image
from pydantic import BaseModel

from app.diagnostic_capture import capture_root, get_session_dir, list_sessions
from app.diagnostic_report import generate_report_from_session_dir
from app.open_dataset_adapters import create_bdd100k_drivable_manifest, create_camvid_manifest
from app.path_dataset_eval import evaluate_path_guidance, load_jsonl
from app.path_dataset_import import create_manifest_from_folders
from app.path_manifest_export import export_session_path_manifest, manifest_to_jsonl


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
    .hero {{ background: linear-gradient(135deg, #1c1c1e, #102033); border: 1px solid #3a3a3c; border-radius: 20px; padding: 20px; margin: 16px 0; }}
    .card {{ background: #1c1c1e; border: 1px solid #3a3a3c; border-radius: 16px; padding: 16px; margin: 16px 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }}
    img {{ max-width: 420px; border-radius: 12px; border: 1px solid #3a3a3c; }}
    input, select, textarea, button {{ font: inherit; margin: 4px; }}
    input, select, textarea {{ background: #2c2c2e; color: #fff; border: 1px solid #555; border-radius: 8px; padding: 8px; }}
    input.wide {{ width: min(860px, calc(100vw - 88px)); box-sizing: border-box; }}
    button {{ background: #0a84ff; color: #fff; border: 0; border-radius: 8px; padding: 8px 12px; }}
    .secondary {{ background: #2c2c2e; color: #f5f5f7; border: 1px solid #555; }}
    .muted {{ color: #a1a1a6; }}
    .hint {{ color: #d1d1d6; max-width: 720px; line-height: 1.45; }}
    .callout {{ background: #102033; border: 1px solid #2f6f9f; border-radius: 16px; padding: 14px; margin: 14px 0; max-width: 900px; }}
    .status {{ background: #111; border: 1px solid #3a3a3c; border-radius: 12px; padding: 12px; margin: 12px 0; max-width: 900px; }}
    .status.ok {{ border-color: #30d158; }}
    .status.error {{ border-color: #ff453a; }}
    .step {{ display: inline-block; width: 28px; height: 28px; border-radius: 50%; background: #0a84ff; color: #fff; text-align: center; line-height: 28px; font-weight: 800; margin-right: 8px; }}
    .pill {{ display: inline-block; background: #2c2c2e; color: #d1d1d6; padding: 3px 8px; border-radius: 999px; margin: 2px; }}
    .danger {{ background: #ff453a; }}
    .label-item {{ background: #111; padding: 10px; border-radius: 12px; margin: 8px 0; }}
    .field-grid {{ display: grid; grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr); gap: 8px; max-width: 760px; }}
    .field-grid label {{ color: #d1d1d6; font-size: 0.92rem; }}
    .field-grid textarea {{ width: 100%; box-sizing: border-box; }}
    .frame-overlay {{ position: relative; display: inline-block; max-width: 420px; }}
    .frame-overlay img {{ display: block; width: 100%; height: auto; }}
    .frame-overlay svg {{ position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }}
    .explain {{ color: #8e8e93; font-size: 0.92rem; margin-top: 4px; }}
    details {{ background: #151518; border: 1px solid #3a3a3c; border-radius: 12px; padding: 10px; margin: 12px 0; }}
    summary {{ cursor: pointer; font-weight: 700; }}
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
            f"<p><a href='/diagnostics/sessions/{sid}/annotate'>标注</a> · "
            f"<a href='/diagnostics/sessions/{sid}/path-guidance/ui'>引导层可视化</a> · "
            f"<a href='/diagnostics/sessions/{sid}/report/ui'>评估报告</a> · "
            f"<button onclick=\"deleteSession('{sid}')\">删除 session</button></p></div>"
        )
    script = """<script>
async function deleteSession(sessionId) {
  if (!confirm('确定删除这个诊断 session 吗？')) return;
  const resp = await fetch('/diagnostics/sessions/' + sessionId, {method: 'DELETE'});
  if (resp.ok) location.reload(); else alert('删除失败');
}
</script>"""
    hero = """
<div class='hero'>
  <h1>VQASee 闭环实验平台</h1>
  <p class='hint'>从真机诊断帧、开源数据集、本地感知层、Mac 后端 Qwen 到评估报告的一站式实验入口。普通用户不会看到这个页面。</p>
  <p><span class='pill'>采集数据</span><span class='pill'>结构化标注</span><span class='pill'>引导层可视化</span><span class='pill'>评估报告</span><span class='pill'>任务建议</span></p>
</div>
"""
    modules = """
<div class='grid'>
  <div class='card'><h2>1. 真机诊断 Sessions</h2><p class='muted'>查看 iPhone 上传的帧、metadata、本地模型输出和 path guidance。</p></div>
  <div class='card'><h2>2. 引导层可视化</h2><p class='muted'>把 LocalPathGuidanceSignal 叠加到图片上，检查通行候选区、风险区和不确定区是否合理。</p></div>
  <div class='card'><h2>3. 评估报告</h2><p class='muted'>自动发现 in-flight、误报、漏报、缺 Qwen raw output、depth/segmentation 能力缺口。</p></div>
  <div class='card'><h2>4. 开源数据集评估</h2><p class='muted'>CLI：<code>python server-vqa/tools/evaluate_path_guidance_dataset.py docs/datasets/path-guidance-manifest-example.jsonl</code></p><p><a href='/diagnostics/datasets/ui'>打开数据集评估</a></p></div>
</div>
"""
    body = hero + modules + "<h2>Sessions</h2>" + script + ("".join(cards) or "<p>暂无 session。</p>")
    return _html_page("VQASee 闭环实验平台", body)


def _manifest_rows(session_dir: Path) -> list[dict]:
    manifest_path = session_dir / "manifest.jsonl"
    if not manifest_path.is_file():
        return []
    rows: list[dict] = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _svg_rect(rect: dict, color: str, opacity: float, dash: str = "") -> str:
    try:
        x = float(rect.get("x", 0)) * 100
        y = (1 - float(rect.get("y", 0)) - float(rect.get("height", 0))) * 100
        w = float(rect.get("width", 0)) * 100
        h = float(rect.get("height", 0)) * 100
    except (TypeError, ValueError):
        return ""
    if w <= 0 or h <= 0:
        return ""
    dash_attr = f" stroke-dasharray='{dash}'" if dash else ""
    return (
        f"<rect x='{x:.2f}' y='{y:.2f}' width='{w:.2f}' height='{h:.2f}' rx='3' "
        f"fill='{color}' fill-opacity='{opacity:.2f}' stroke='{color}' stroke-width='1.2' stroke-opacity='0.85'{dash_attr}/>"
    )


def _svg_corridor(rect: dict, status: str) -> str:
    color = {
        "blocked": "#ff453a",
        "caution": "#ffd60a",
        "unknown": "#8e8e93",
        "candidateOpen": "#64d2ff",
    }.get(status, "#8e8e93")
    try:
        min_y = float(rect.get("y", 0))
        max_y = min_y + float(rect.get("height", 0))
    except (TypeError, ValueError):
        return ""
    bottom_y = (1 - min_y) * 100
    top_y = (1 - min(max_y, 0.62)) * 100
    points = f"30,{bottom_y:.2f} 42,{top_y:.2f} 58,{top_y:.2f} 70,{bottom_y:.2f}"
    opacity = 0.08 if status == "candidateOpen" else 0.20
    return (
        f"<polygon points='{points}' fill='{color}' fill-opacity='{opacity:.2f}' "
        f"stroke='{color}' stroke-width='1.5' stroke-opacity='0.85' stroke-dasharray='4 3'/>"
        f"<line x1='50' y1='{bottom_y:.2f}' x2='50' y2='{top_y:.2f}' stroke='{color}' "
        f"stroke-width='0.8' stroke-opacity='0.65' stroke-dasharray='3 3'/>"
    )


def _path_guidance_svg(path_guidance: dict) -> str:
    if not isinstance(path_guidance, dict) or not path_guidance:
        return "<svg viewBox='0 0 100 100'></svg>"
    parts: list[str] = []
    status = str(path_guidance.get("near_path_status", "unknown"))
    corridor = path_guidance.get("guidance_corridor")
    blocked = path_guidance.get("blocked_regions") if isinstance(path_guidance.get("blocked_regions"), list) else []
    uncertain = path_guidance.get("uncertain_regions") if isinstance(path_guidance.get("uncertain_regions"), list) else []
    if corridor and not (status == "candidateOpen" and not blocked and not uncertain):
        parts.append(_svg_corridor(corridor, status))
    else:
        parts.append("<line x1='50' y1='88' x2='50' y2='58' stroke='#64d2ff' stroke-width='0.8' stroke-opacity='0.28' stroke-dasharray='3 4'/>")
    for rect in uncertain:
        if isinstance(rect, dict):
            parts.append(_svg_rect(rect, "#8e8e93", 0.20, "3 3"))
    for rect in blocked:
        if isinstance(rect, dict):
            parts.append(_svg_rect(rect, "#ff453a", 0.18, "4 3"))
    return "<svg viewBox='0 0 100 100' preserveAspectRatio='none'>" + "".join(parts) + "</svg>"


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
<p><a href='/diagnostics/ui'>← 返回 sessions</a> · <a href='/diagnostics/sessions/{html.escape(session_id)}/annotate'>打开标注</a> · <a href='/diagnostics/sessions/{html.escape(session_id)}/path-guidance/ui'>引导层可视化</a></p>
<h1>评估报告：{html.escape(session_id)}</h1>
<p class='hint'>这份报告给乔布斯/罗根/思余/全麦看，用于发现产品、系统、UI 和模型问题，不给普通用户看。</p>
<div class='card'><h2>核心结论</h2><p>{html.escape(str(report.get('headline', '')))}</p></div>
<div class='card'><h2>关键指标</h2>{metric_html}<h3>本地检测对象</h3>{object_html}<h3>人工标注</h3>{label_html}</div>
<div class='card'><h2>自动发现的问题</h2>{finding_html}</div>
<div class='card'><h2>建议任务卡</h2>{task_html}</div>
"""
    return _html_page(f"评估报告 {session_id}", body)


@router.get("/sessions/{session_id}/path-manifest", response_class=PlainTextResponse)
def session_path_manifest(session_id: str):
    session_dir = get_session_dir(session_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    rows = export_session_path_manifest(session_id=session_id, session_dir=session_dir)
    return PlainTextResponse(manifest_to_jsonl(rows), media_type="application/x-ndjson")


@router.get("/sessions/{session_id}/path-eval")
def session_path_eval(session_id: str) -> dict:
    session_dir = get_session_dir(session_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    rows = export_session_path_manifest(session_id=session_id, session_dir=session_dir)
    return evaluate_path_guidance(rows)


@router.get("/sessions/{session_id}/path-eval/ui", response_class=HTMLResponse)
def session_path_eval_ui(session_id: str):
    report = session_path_eval(session_id)
    metrics = "".join(
        f"<span class='pill'>{html.escape(str(key))}: {html.escape(str(value))}</span>"
        for key, value in report.items()
        if key not in {"status_confusion", "direction_confusion", "risk_misses", "false_blocks", "missing_predictions", "recommendations"}
    )
    details = html.escape(json.dumps(report, ensure_ascii=False, indent=2))
    body = f"""
<p><a href='/diagnostics/ui'>← 返回 sessions</a> · <a href='/diagnostics/sessions/{html.escape(session_id)}/path-guidance/ui'>引导层可视化</a> · <a href='/diagnostics/sessions/{html.escape(session_id)}/path-manifest'>下载 manifest</a></p>
<h1>路径评估：{html.escape(session_id)}</h1>
<div class='card'><h2>指标</h2>{metrics}</div>
<div class='card'><h2>完整报告</h2><pre>{details}</pre></div>
"""
    return _html_page(f"路径评估 {session_id}", body)


def _dataset_manifest_candidates() -> list[Path]:
    roots = [Path("docs/datasets")]
    configured = os.getenv("VQASEE_DATASET_MANIFEST_DIR", "").strip()
    if configured:
        roots.append(Path(configured).expanduser())
    candidates: list[Path] = []
    for root in roots:
        if root.is_dir():
            candidates.extend(sorted(root.glob("*.jsonl")))
    return candidates


@router.get("/datasets/ui", response_class=HTMLResponse)
def datasets_ui():
    cards = []
    for path in _dataset_manifest_candidates():
        safe_name = html.escape(path.name)
        encoded = html.escape(str(path))
        cards.append(
            f"<div class='card'><h2>{safe_name}</h2>"
            f"<p class='muted'>{html.escape(str(path))}</p>"
            f"<p><a href='/diagnostics/datasets/manifest/ui?manifest={encoded}'>浏览</a> · <a href='/diagnostics/datasets/evaluate/ui?manifest={encoded}'>评估</a></p></div>"
        )
    body = (
        "<p><a href='/diagnostics/ui'>← 返回平台首页</a></p>"
        "<h1>开源/本地数据集评估</h1>"
        "<p><a href='/diagnostics/datasets/create-open/ui'>接入开源数据集</a> · <a href='/diagnostics/datasets/create/ui'>从图片+mask目录创建 manifest</a></p>"
        "<p class='hint'>开源数据集先使用本地已下载数据；平台不自动下载大文件，也不绕过数据集 license。生成的 path manifest 放到 docs/datasets/ 或 VQASEE_DATASET_MANIFEST_DIR 后可在这里评估。</p>"
        + ("".join(cards) or "<p>暂无 manifest。示例：docs/datasets/path-guidance-manifest-example.jsonl</p>")
    )
    return _html_page("数据集评估", body)


def _allowed_local_roots() -> list[Path]:
    roots = [Path.cwd().resolve(), Path("/private/tmp").resolve(), Path("/tmp").resolve()]
    configured = os.getenv("VQASEE_DATASET_ROOT", "").strip()
    if configured:
        roots.append(Path(configured).expanduser().resolve())
    return roots


def _safe_local_file(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file_not_found")
    if not any(root == path or root in path.parents for root in _allowed_local_roots()):
        raise HTTPException(status_code=403, detail="file_not_allowed")
    return path


@router.get("/local-file")
def local_file(path: str):
    file_path = _safe_local_file(path)
    media = "image/png" if file_path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(file_path, media_type=media)


def default_tags_for_dataset(dataset_type: str) -> str:
    if dataset_type == "road":
        return "road,vehicle,drivable"
    if dataset_type == "outdoor":
        return "outdoor,sidewalk,curb"
    return "indoor,floor,office"


@router.get("/datasets/create-open/ui", response_class=HTMLResponse)
def dataset_create_open_ui():
    body = """
<p><a href='/diagnostics/datasets/ui'>← 返回数据集评估</a></p>
<h1>接入开源数据集</h1>
<p class='hint'>先从免账号、可直接获取的小型 GitHub 数据集跑通闭环；需要账号/license 的大数据集放到高级路径。</p>

<div class='callout'>
  <h2><span class='step'>1</span>一键下载 CamVid GitHub 数据并生成 manifest</h2>
  <p class='hint'>推荐先点这个。平台会从 GitHub 下载公开 CamVid 镜像到 <code>/tmp/vqasee-open-datasets/camvid</code>，读取道路/人行道语义标签，生成 VQASee path-guidance manifest。</p>
  <button id='downloadCamvidButton' type='button' onclick='downloadCamvid()'>下载 CamVid 并生成 manifest</button>
  <div id='downloadStatus' class='status' style='display:none'></div>
  <p class='explain'>如果网络慢或 GitHub 不可达，页面会显示失败原因，不会只让浏览器一直转圈。也可以先用下面的“内置演示”确认流程。</p>
  <form action='/diagnostics/datasets/create-open-demo' method='get'>
    <button class='secondary' type='submit'>只生成本地演示 manifest</button>
  </form>
</div>
<script>
async function downloadCamvid() {
  const button = document.getElementById('downloadCamvidButton');
  const status = document.getElementById('downloadStatus');
  button.disabled = true;
  status.style.display = 'block';
  status.className = 'status';
  let seconds = 0;
  status.innerHTML = '正在连接 GitHub 并下载 CamVid… 已等待 0 秒。<br><span class="muted">如果网络较慢，可以先跑本地演示；失败后这里会显示原因。</span>';
  const timer = setInterval(() => {
    seconds += 1;
    status.innerHTML = `正在连接 GitHub 并下载 CamVid… 已等待 ${seconds} 秒。<br><span class="muted">超过 30 秒仍无结果，通常是 GitHub 网络慢；你可以打开本地演示或稍后重试。</span>`;
  }, 1000);
  try {
    const response = await fetch('/diagnostics/datasets/download-open?dataset=camvid&as_json=true', {headers: {'Accept': 'application/json'}});
    const text = await response.text();
    let payload = {};
    try { payload = JSON.parse(text); } catch (_) { payload = {detail: text}; }
    clearInterval(timer);
    if (!response.ok) {
      status.className = 'status error';
      status.innerHTML = `下载失败：${payload.detail || response.statusText}<br><br><a href="/diagnostics/datasets/create-open-demo">先打开本地演示 manifest</a>`;
      return;
    }
    status.className = 'status ok';
    const manifest = encodeURIComponent(payload.manifest);
    status.innerHTML = `已生成 ${payload.rows} 行 manifest。<br><a href="/diagnostics/datasets/manifest/ui?manifest=${manifest}">打开 manifest 浏览</a> · <a href="/diagnostics/datasets/evaluate/ui?manifest=${manifest}">直接评估</a>`;
  } catch (error) {
    clearInterval(timer);
    status.className = 'status error';
    status.innerHTML = `下载请求失败：${error}<br><br><a href="/diagnostics/datasets/create-open-demo">先打开本地演示 manifest</a>`;
  } finally {
    button.disabled = false;
  }
}
</script>

<div class='card'>
  <h2><span class='step'>2</span>已有 CamVid 本地目录？直接生成</h2>
  <form action='/diagnostics/datasets/create-open' method='get'>
    <input type='hidden' name='dataset' value='camvid'>
    <p><label>CamVid 图片目录<br><input class='wide' name='images' placeholder='/tmp/vqasee-open-datasets/camvid/CamVid_RGB' required></label></p>
    <p><label>CamVid RGB 标签目录<br><input class='wide' name='labels' placeholder='/tmp/vqasee-open-datasets/camvid/CamVid_Label' required></label></p>
    <details>
      <summary>高级设置</summary>
      <p><label>输出 manifest<br><input class='wide' name='output' placeholder='docs/datasets/camvid-manifest.jsonl'></label></p>
      <p><label>Split <input name='split' value='road'></label> <label>Limit（0=全部）<input name='limit' value='0'></label></p>
    </details>
    <button type='submit'>生成 CamVid manifest</button>
  </form>
</div>

<div class='card'>
  <h2><span class='step'>3</span>高级：接入 BDD100K 大数据集</h2>
  <p class='hint'>BDD100K 更适合道路/驾驶风险，但官方数据通常需要账号、license 和大文件下载。这里保留给你本地已经下载好的情况，不再作为默认入口。</p>
  <form action='/diagnostics/datasets/create-open' method='get'>
    <input type='hidden' name='dataset' value='bdd100k_drivable'>
    <p><label>图片目录<br><input class='wide' name='images' placeholder='/tmp/bdd100k/images/100k/val' required></label></p>
    <p><label>Labels JSON<br><input class='wide' name='labels' placeholder='/tmp/bdd100k/labels/bdd100k_labels_images_val.json' required></label></p>
    <details>
      <summary>高级设置</summary>
      <p class='muted'>默认会写入 docs/datasets/bdd100k-drivable-manifest.jsonl。</p>
      <p><label>输出 manifest<br><input class='wide' name='output' placeholder='docs/datasets/bdd100k-drivable-manifest.jsonl'></label></p>
      <p><label>Split <input name='split' value='road'></label> <label>Limit（0=全部）<input name='limit' value='0'></label></p>
    </details>

    <button class='secondary' type='submit'>生成 BDD100K manifest</button>
  </form>
</div>
<p class='hint'>安全限制：平台只允许读取仓库目录、/tmp、/private/tmp 或 VQASEE_DATASET_ROOT 下的本地文件。</p>
"""
    return _html_page("接入开源数据集", body)


def _open_dataset_root() -> Path:
    configured = os.getenv("VQASEE_DATASET_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path("/tmp/vqasee-open-datasets").resolve()


def _extract_zip_flat(zip_path: Path, output_dir: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(output_dir)
    # GitHub archives normally extract into a single top-level folder. Move the
    # contents up so users see a stable path regardless of branch hash/name.
    children = [path for path in output_dir.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        top = children[0]
        for child in top.iterdir():
            target = output_dir / child.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            child.rename(target)
        top.rmdir()


def _download_url_to_file(url: str, output_path: Path, *, timeout_seconds: int = 30) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "VQASee-diagnostics/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response, output_path.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


@router.get("/datasets/download-open")
def dataset_download_open(dataset: str = "camvid", output: str = "", limit: int = 0, as_json: bool = False):
    if dataset != "camvid":
        raise HTTPException(status_code=400, detail="unsupported_download_dataset")
    root = _open_dataset_root() / "camvid"
    root.mkdir(parents=True, exist_ok=True)
    zip_path = root / "camvid.zip"
    url = "https://github.com/lih627/CamVid/archive/refs/heads/master.zip"
    if not (root / "CamVid_RGB").is_dir() or not (root / "CamVid_Label").is_dir():
        try:
            _download_url_to_file(url, zip_path, timeout_seconds=30)
            _extract_zip_flat(zip_path, root)
        except Exception as exc:  # pragma: no cover - network failures are environment-specific.
            raise HTTPException(status_code=502, detail=f"download_failed: {exc}") from exc
        finally:
            zip_path.unlink(missing_ok=True)
    output_path = Path(output or "docs/datasets/camvid-manifest.jsonl").expanduser()
    rows = create_camvid_manifest(
        images_dir=root / "CamVid_RGB",
        labels_dir=root / "CamVid_Label",
        output_path=output_path,
        split="road",
        limit=limit,
    )
    if as_json:
        return {"status": "ok", "dataset": "camvid", "rows": len(rows), "manifest": str(output_path), "dataset_root": str(root)}
    return RedirectResponse(url=f"/diagnostics/datasets/manifest/ui?manifest={html.escape(str(output_path))}", status_code=303)


@router.get("/datasets/create-open-demo")
def dataset_create_open_demo(as_json: bool = False):
    demo_root = Path("/tmp/vqasee-open-dataset-demo").resolve()
    images_dir = demo_root / "bdd100k" / "images" / "100k" / "val"
    labels_path = demo_root / "bdd100k" / "labels" / "bdd100k_labels_images_val.json"
    output_path = Path("docs/datasets/bdd100k-demo-manifest.jsonl")
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    image_path = images_dir / "demo-road.jpg"
    if not image_path.is_file():
        image = Image.new("RGB", (320, 180), "#1c1c1e")
        image.save(image_path)
    labels_path.write_text(
        json.dumps(
            [
                {
                    "name": image_path.name,
                    "attributes": {"scene": "city street", "timeofday": "daytime", "weather": "clear"},
                    "labels": [
                        {
                            "category": "drivable area",
                            "attributes": {"areaType": "direct"},
                            "poly2d": [{"vertices": [[80, 70], [240, 70], [318, 178], [2, 178]], "types": "LLLL", "closed": True}],
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = create_bdd100k_drivable_manifest(
        images_dir=images_dir,
        labels_path=labels_path,
        output_path=output_path,
        split="road-demo",
        limit=1,
    )
    if as_json:
        return {"status": "ok", "rows": len(rows), "manifest": str(output_path), "demo_root": str(demo_root)}
    return RedirectResponse(url=f"/diagnostics/datasets/manifest/ui?manifest={html.escape(str(output_path))}", status_code=303)


@router.get("/datasets/create-open")
def dataset_create_open(dataset: str, images: str, labels: str, output: str = "", split: str = "road", limit: int = 0, as_json: bool = False):
    if dataset not in {"bdd100k_drivable", "camvid"}:
        raise HTTPException(status_code=400, detail="unsupported_open_dataset")
    images_dir = Path(images).expanduser()
    labels_path = Path(labels).expanduser()
    default_output = "docs/datasets/camvid-manifest.jsonl" if dataset == "camvid" else "docs/datasets/bdd100k-drivable-manifest.jsonl"
    output_path = Path(output or default_output).expanduser()
    for path in [images_dir, labels_path]:
        resolved = path.resolve()
        if not any(root == resolved or root in resolved.parents for root in _allowed_local_roots()):
            raise HTTPException(status_code=403, detail=f"path_not_allowed: {path}")
    output_parent = output_path.parent.resolve()
    if not any(root == output_parent or root in output_parent.parents for root in _allowed_local_roots()):
        raise HTTPException(status_code=403, detail=f"output_not_allowed: {output_path}")
    if dataset == "camvid":
        rows = create_camvid_manifest(images_dir=images_dir, labels_dir=labels_path, output_path=output_path, split=split.strip() or "road", limit=limit)
    else:
        rows = create_bdd100k_drivable_manifest(
            images_dir=images_dir,
            labels_path=labels_path,
            output_path=output_path,
            split=split.strip() or "road",
            limit=limit,
        )
    if as_json:
        return {"status": "ok", "dataset": dataset, "rows": len(rows), "manifest": str(output_path)}
    return RedirectResponse(url=f"/diagnostics/datasets/manifest/ui?manifest={html.escape(str(output_path))}", status_code=303)


@router.get("/datasets/create/ui", response_class=HTMLResponse)
def dataset_create_ui():
    body = """
<p><a href='/diagnostics/datasets/ui'>← 返回数据集评估</a></p>
<h1>创建数据集 manifest</h1>
<p class='hint'>主流程只需要选择数据类型和目录；平台会自动生成 manifest 路径、split 和 tags。高级设置仅供开发者调试，普通测试不用展开。</p>
<div class='card'>
  <form action='/diagnostics/datasets/create' method='get'>
    <h2>1. 选择数据类型</h2>
    <p><label><input type='radio' name='dataset_type' value='indoor' checked> 室内：办公室 / 走廊 / 地面 / 水桶 / 椅子</label></p>
    <p><label><input type='radio' name='dataset_type' value='outdoor'> 室外：人行道 / 路沿 / 户外障碍</label></p>
    <p><label><input type='radio' name='dataset_type' value='road'> 道路/驾驶风险：马路 / 车辆 / 车道 / 可行驶区域</label></p>

    <h2>2. 选择本地数据</h2>
    <p><label>图片目录<br><input name='images' size='80' placeholder='/path/to/images' required></label></p>
    <p><label>Mask 目录（可选，白=可通行）<br><input name='masks' size='80' placeholder='/path/to/masks'></label></p>

    <details>
      <summary>高级设置</summary>
      <p class='muted'>开发者调试用：不填则自动生成。普通测试请保持默认。</p>
      <p><label>输出 manifest（默认：docs/datasets/auto-数据类型-manifest.jsonl）<br><input name='output' size='80' placeholder='docs/datasets/auto-indoor-manifest.jsonl'></label></p>
      <p><label>Split（默认跟随数据类型）<input name='split' placeholder='indoor/outdoor/road'></label></p>
      <p><label>Tags（默认自动生成）<input name='tags' placeholder='indoor,floor,office'></label></p>
      <p><label>Mask 阈值（默认 0.5）<input name='threshold' value='0.5'></label> <label>Limit（0=全部）<input name='limit' value='0'></label></p>
    </details>

    <button type='submit'>生成数据集 manifest</button>
  </form>
</div>
<p class='hint'>安全限制：平台只允许读取仓库目录、/tmp、/private/tmp 或 VQASEE_DATASET_ROOT 下的本地文件。</p>
"""
    return _html_page("创建数据集 manifest", body)


@router.get("/datasets/create")
def dataset_create(images: str, output: str = "", masks: str = "", dataset_type: str = "indoor", split: str = "", tags: str = "", threshold: float = 0.5, limit: int = 0, as_json: bool = False):
    images_dir = Path(images).expanduser()
    masks_dir = Path(masks).expanduser() if masks.strip() else None
    safe_type = dataset_type if dataset_type in {"indoor", "outdoor", "road"} else "indoor"
    split = split.strip() or safe_type
    if not output.strip():
        output = f"docs/datasets/auto-{safe_type}-manifest.jsonl"
    output_path = Path(output).expanduser()
    allowed_dirs = [images_dir]
    if masks_dir:
        allowed_dirs.append(masks_dir)
    for directory in allowed_dirs:
        resolved = directory.resolve()
        if not any(root == resolved or root in resolved.parents for root in _allowed_local_roots()):
            raise HTTPException(status_code=403, detail=f"dir_not_allowed: {directory}")
    rows = create_manifest_from_folders(
        images_dir=images_dir,
        masks_dir=masks_dir,
        output_path=output_path,
        split=split,
        scene_tags=[tag.strip() for tag in (tags or default_tags_for_dataset(safe_type)).split(",") if tag.strip()],
        threshold=threshold,
        limit=limit,
    )
    if as_json:
        return {"status": "ok", "rows": len(rows), "manifest": str(output_path)}
    return RedirectResponse(url=f"/diagnostics/datasets/manifest/ui?manifest={html.escape(str(output_path))}", status_code=303)


@router.get("/datasets/manifest/ui", response_class=HTMLResponse)
def dataset_manifest_ui(manifest: str):
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")
    rows = load_jsonl(manifest_path)
    cards = []
    for row in rows[:200]:
        frame_id = html.escape(str(row.get("frame_id", "")))
        image_path = str(row.get("image_path") or "")
        mask_path = str(row.get("mask_path") or "")
        image_html = "<p class='muted'>无可预览图片路径</p>"
        if image_path:
            image_html = f"<img src='/diagnostics/local-file?path={html.escape(image_path)}' alt='{frame_id}'>"
        mask_html = ""
        if mask_path:
            mask_html = f"<div><h3>Mask</h3><img src='/diagnostics/local-file?path={html.escape(mask_path)}' alt='mask {frame_id}'></div>"
        gt_raw = row.get("ground_truth", {})
        pred_raw = row.get("prediction", row.get("path_guidance", {}))
        gt = html.escape(json.dumps(gt_raw, ensure_ascii=False, indent=2))
        pred = html.escape(json.dumps(pred_raw, ensure_ascii=False, indent=2))
        coverage = html.escape(json.dumps(row.get("mask_coverage", {}), ensure_ascii=False, indent=2))
        cards.append(
            f"""<div class='card'><h2>{frame_id}</h2><div class='row'><div>{image_html}</div>{mask_html}<div><h3>真实答案 Ground Truth</h3><p class='hint'>由 mask 或人工标注生成，表示这一帧真实的通行状态。</p><pre>{gt}</pre><h3>Mask 覆盖率</h3><p class='hint'>每个区域中白色/可通行像素比例。</p><pre>{coverage}</pre><h3>VQASee 预测 Prediction</h3><p class='hint'>模型/算法输出。若为空，说明还没对该 manifest 跑 prediction。</p><pre>{pred}</pre></div></div></div>"""
        )
    body = (
        f"<p><a href='/diagnostics/datasets/ui'>← 返回数据集评估</a> · <a href='/diagnostics/datasets/evaluate/ui?manifest={html.escape(str(manifest_path))}'>评估此 manifest</a></p>"
        f"<h1>Manifest 浏览：{html.escape(manifest_path.name)}</h1>"
        + ("".join(cards) or "<p>manifest 为空。</p>")
    )
    return _html_page("Manifest 浏览", body)


@router.get("/datasets/evaluate")
def dataset_evaluate(manifest: str) -> dict:
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")
    return evaluate_path_guidance(load_jsonl(manifest_path))


@router.get("/datasets/evaluate/ui", response_class=HTMLResponse)
def dataset_evaluate_ui(manifest: str):
    report = dataset_evaluate(manifest)
    details = html.escape(json.dumps(report, ensure_ascii=False, indent=2))
    def card(title: str, value: object, hint: str = "") -> str:
        return f"<div class='card'><h2>{html.escape(title)}</h2><p style='font-size:2rem;font-weight:800'>{html.escape(str(value))}</p><p class='muted'>{html.escape(hint)}</p></div>"
    cards = "<div class='grid'>" + "".join([
        card("总帧数", report.get("frame_count"), "manifest 中的总图像/帧数"),
        card("有标注帧", report.get("labeled_frames"), "可参与准确率计算的帧"),
        card("状态准确率", report.get("status_accuracy"), "near/left/right 三个区域的状态匹配率"),
        card("方向准确率", report.get("focus_direction_accuracy"), "关注方向是否匹配"),
        card("漏报风险", report.get("risk_miss_count"), "真实 caution/blocked 却预测 candidateOpen"),
        card("误阻挡", report.get("false_block_count"), "真实 candidateOpen 却预测 caution/blocked"),
        card("Unknown 比例", report.get("unknown_prediction_rate"), "预测为 unknown 的比例"),
    ]) + "</div>"
    recs = "".join(f"<li>{html.escape(str(item))}</li>" for item in report.get("recommendations", []))
    body = f"""
<p><a href='/diagnostics/datasets/ui'>← 返回数据集评估</a> · <a href='/diagnostics/datasets/manifest/ui?manifest={html.escape(manifest)}'>浏览 manifest</a></p>
<h1>数据集评估：{html.escape(Path(manifest).name)}</h1>
{cards}
<div class='card'><h2>建议</h2><ul>{recs}</ul></div>
<details><summary>完整 JSON 报告</summary><pre>{details}</pre></details>
"""
    return _html_page("数据集评估报告", body)


@router.get("/sessions/{session_id}/path-guidance/ui", response_class=HTMLResponse)
def path_guidance_ui(session_id: str):
    session_dir = get_session_dir(session_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    rows = _manifest_rows(session_dir)
    cards: list[str] = []
    for row in rows[:200]:
        frame = str(row.get("backend_saved_frame") or row.get("frame") or "")
        if not frame.endswith(".jpg"):
            continue
        frame_name = Path(frame).name
        safe_frame = html.escape(frame_name)
        perception = row.get("perception") if isinstance(row.get("perception"), dict) else {}
        path_guidance = perception.get("path_guidance") if isinstance(perception.get("path_guidance"), dict) else {}
        svg = _path_guidance_svg(path_guidance)
        metrics = html.escape(json.dumps(path_guidance or {}, ensure_ascii=False, indent=2))
        event = html.escape(str(row.get("event", "unknown")))
        reason = html.escape(str(row.get("reason", "")))
        cards.append(
            f"""<div class='card'>
  <h2>{safe_frame}</h2>
  <p><span class='pill'>event: {event}</span><span class='pill'>{reason}</span></p>
  <div class='row'>
    <div class='frame-overlay'>
      <img src='/diagnostics/sessions/{html.escape(session_id)}/frames/{safe_frame}' alt='{safe_frame}'>
      {svg}
    </div>
    <div>
      <h3>path_guidance</h3>
      <pre>{metrics}</pre>
      <p class='hint'>蓝/青：通行候选参考；黄：需要注意；红：疑似被占用；灰：信息不足。没有真实 depth/segmentation 时，不应把轻参考线理解为路线。</p>
    </div>
  </div>
</div>"""
        )
    body = (
        f"<p><a href='/diagnostics/ui'>← 返回 sessions</a> · "
        f"<a href='/diagnostics/sessions/{html.escape(session_id)}/annotate'>打开标注</a> · "
        f"<a href='/diagnostics/sessions/{html.escape(session_id)}/report/ui'>评估报告</a></p>"
        f"<h1>引导层可视化：{html.escape(session_id)}</h1>"
        "<p class='hint'>此页面用于开发/评估，把 LocalPathGuidanceSignal 叠加到诊断帧上，帮助判断 overlay 是否合理。</p>"
        + ("".join(cards) or "<p>暂无可视化帧。</p>")
    )
    return _html_page(f"引导层可视化 {session_id}", body)


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
    body = f"<p><a href='/diagnostics/ui'>← 返回 sessions</a> · <a href='/diagnostics/sessions/{html.escape(session_id)}/report/ui'>查看评估报告</a> · <a href='/diagnostics/sessions/{html.escape(session_id)}/path-guidance/ui'>引导层可视化</a></p><h1>标注 session: {html.escape(session_id)}</h1>" + help_text + script + "".join(rows)
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
