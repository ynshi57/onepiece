"""HTTP API for local diagnostic capture management."""

from __future__ import annotations

import html
import io
import json
import os
import shutil
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from PIL import Image
from pydantic import BaseModel

from app.diagnostic_capture import capture_root, get_session_dir, list_sessions
from app.diagnostic_report import generate_report_from_session_dir
from app.eval_baseline import list_baselines, load_baseline, save_baseline
from app.open_dataset_adapters import create_bdd100k_drivable_manifest, create_camvid_manifest
from app.path_dataset_eval import evaluate_path_guidance, load_jsonl
from app.path_dataset_import import create_manifest_from_folders
from app.path_manifest_export import export_session_path_manifest, manifest_to_jsonl
from app.path_parity import compute_parity
from app.perception_config import (
    ConfigValidationError,
    bump_and_save,
    config_store_path,
    load_active_config,
)
from app.traversability_predictor import TraversabilityPredictor, predict_manifest


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


def _missing_prediction_card(missing: int, labeled: int) -> str:
    """Render a prominent card for frames that have no prediction.

    Never hide un-predicted frames inside an average: an evaluator that silently
    skips them would report misleadingly high accuracy. This surfaces them.
    """
    missing = int(missing or 0)
    labeled = int(labeled or 0)
    if missing <= 0:
        return (
            "<div class='card' style='border-color:#30d158'>"
            "<h2>预测覆盖</h2>"
            "<p style='font-size:2rem;font-weight:800'>全部已预测</p>"
            "<p class='muted'>每个有标注的帧都有预测，指标可信。</p></div>"
        )
    ratio = f"{(missing / labeled):.0%}" if labeled else "N/A"
    return (
        "<div class='card' style='border-color:#ff453a'>"
        "<h2>缺预测帧（missing_prediction_count）</h2>"
        f"<p style='font-size:2rem;font-weight:800;color:#ff453a'>{missing}</p>"
        f"<p class='muted'>占有标注帧的 {ratio}。这些帧没有跑出预测，未计入正确率——先对该 manifest 运行预测，指标才可信。</p></div>"
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


@router.post("/sessions/{session_id}/close-loop")
def session_close_loop(session_id: str) -> dict:
    """One-click close-loop: export path manifest, evaluate, save a baseline.

    This turns the diagnostic session into reproducible evidence: the same
    export -> evaluate -> baseline path a release gate later compares against.
    """
    session_dir = get_session_dir(session_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="session_not_found")
    rows = export_session_path_manifest(session_id=session_id, session_dir=session_dir)
    report = evaluate_path_guidance(rows)
    baseline_name = f"session-{session_id}"
    baseline_path = save_baseline(baseline_name, report, source=f"session:{session_id}")
    return {
        "status": "ok",
        "baseline": baseline_name,
        "baseline_path": str(baseline_path),
        "report": report,
    }


@router.get("/baselines")
def baselines() -> dict:
    return {"baselines": list_baselines()}


@router.get("/sessions/{session_id}/path-eval/ui", response_class=HTMLResponse)
def session_path_eval_ui(session_id: str):
    report = session_path_eval(session_id)
    metrics = "".join(
        f"<span class='pill'>{html.escape(str(key))}: {html.escape(str(value))}</span>"
        for key, value in report.items()
        if key not in {"status_confusion", "direction_confusion", "risk_misses", "false_blocks", "missing_predictions", "recommendations"}
    )
    details = html.escape(json.dumps(report, ensure_ascii=False, indent=2))
    missing = report.get("missing_prediction_count") or 0
    labeled = report.get("labeled_frames") or 0
    missing_card = _missing_prediction_card(missing, labeled)
    script = f"""<script>
async function closeLoop() {{
  const status = document.getElementById('closeLoopStatus');
  status.style.display = 'block';
  status.className = 'status';
  status.textContent = '正在导出 manifest、评估并保存基线…';
  try {{
    const resp = await fetch('/diagnostics/sessions/{html.escape(session_id)}/close-loop', {{method: 'POST'}});
    const payload = await resp.json();
    if (!resp.ok) {{ status.className = 'status error'; status.textContent = '失败：' + (payload.detail || resp.statusText); return; }}
    status.className = 'status ok';
    status.innerHTML = '已保存基线 <b>' + payload.baseline + '</b>（' + payload.baseline_path + '）。刷新后可用于回归对比。';
  }} catch (error) {{ status.className = 'status error'; status.textContent = '请求失败：' + error; }}
}}
</script>"""
    body = f"""
<p><a href='/diagnostics/ui'>← 返回 sessions</a> · <a href='/diagnostics/sessions/{html.escape(session_id)}/path-guidance/ui'>引导层可视化</a> · <a href='/diagnostics/sessions/{html.escape(session_id)}/path-manifest'>下载 manifest</a></p>
<h1>路径评估：{html.escape(session_id)}</h1>
{script}
<div class='card'><h2>一键闭环</h2><p class='hint'>导出 path manifest → 跑路径评估 → 存为回归基线，一步完成。</p><button onclick='closeLoop()'>导出 → 评估 → 存基线</button><div id='closeLoopStatus' class='status' style='display:none'></div></div>
{missing_card}
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
            f"<p><a href='/diagnostics/datasets/manifest/ui?manifest={encoded}'>浏览</a> · <a href='/diagnostics/datasets/evaluate/ui?manifest={encoded}'>服务器代理评估</a> · <a href='/diagnostics/datasets/ios-harness/ui?manifest={encoded}'>iPhone 真身评估</a></p></div>"
        )
    body = (
        "<p><a href='/diagnostics/ui'>← 返回平台首页</a></p>"
        "<h1>开源/本地数据集评估</h1>"
        "<p><a href='/diagnostics/datasets/create-open/ui'>接入开源数据集</a> · <a href='/diagnostics/datasets/create/ui'>从图片+mask目录创建 manifest</a> · <a href='/diagnostics/perception-config/ui'>感知配置（OTA）</a></p>"
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
def local_file(path: str, w: int = 0):
    file_path = _safe_local_file(path)
    # w>0 → serve a downscaled JPEG thumbnail so browse pages load fast instead
    # of pulling hundreds of full-size (~1MB) PNGs. Full image still available
    # by opening the same URL without w.
    if w and w > 0:
        max_width = min(w, 1600)
        try:
            with Image.open(file_path) as image:
                image = image.convert("RGB")
                image.thumbnail((max_width, max_width * 4))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=82)
        except OSError as exc:
            raise HTTPException(status_code=422, detail=f"thumbnail_failed: {exc}") from exc
        return Response(content=buffer.getvalue(), media_type="image/jpeg")
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
    cam_images, cam_labels = _detect_camvid_dirs()
    detected = bool(cam_images and cam_labels)
    images_val = html.escape(str(cam_images)) if cam_images else ""
    labels_val = html.escape(str(cam_labels)) if cam_labels else ""
    if detected:
        step2_banner = "<div class='status ok'>已检测到本地 CamVid，目录已自动填好，直接点“生成 CamVid manifest”即可，无需手填路径。</div>"
    else:
        step2_banner = "<p class='hint'>用第 1 步下载过后，这里会自动识别目录。也可手动填入包含 CamVid_RGB / CamVid_Label 的目录（留空则自动识别）。</p>"
    step2_card = f"""<div class='card'>
  <h2><span class='step'>2</span>用已下载的 CamVid 生成 manifest</h2>
  {step2_banner}
  <form action='/diagnostics/datasets/create-open' method='get'>
    <input type='hidden' name='dataset' value='camvid'>
    <p><label>CamVid 图片目录（留空自动识别）<br><input class='wide' name='images' value='{images_val}' placeholder='自动识别 CamVid_RGB'></label></p>
    <p><label>CamVid RGB 标签目录（留空自动识别）<br><input class='wide' name='labels' value='{labels_val}' placeholder='自动识别 CamVid_Label'></label></p>
    <details>
      <summary>高级设置</summary>
      <p><label>输出 manifest<br><input class='wide' name='output' placeholder='docs/datasets/camvid-manifest.jsonl'></label></p>
      <p><label>Split <input name='split' value='road'></label> <label>Limit（0=全部）<input name='limit' value='0'></label></p>
    </details>
    <button type='submit'>生成 CamVid manifest</button>
  </form>
</div>"""
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

{{STEP2_CARD}}

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
    body = body.replace("{{STEP2_CARD}}", step2_card)
    return _html_page("接入开源数据集", body)


def _open_dataset_root() -> Path:
    configured = os.getenv("VQASEE_DATASET_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path("/tmp/vqasee-open-datasets").resolve()


def _detect_camvid_dirs() -> tuple[Path | None, Path | None]:
    """Find an already-downloaded CamVid's RGB/label dirs, tolerant of nesting.

    Lets the UI auto-fill Step 2 instead of asking the user to type paths that
    depend on the archive's internal folder (e.g. CamVid-main/CamVid_RGB).
    """
    root = _open_dataset_root() / "camvid"
    if not root.is_dir():
        return None, None
    return _find_dataset_dir(root, "CamVid_RGB"), _find_dataset_dir(root, "CamVid_Label")


def _resolve_camvid_subdir(path_text: str, name: str) -> Path | None:
    """Resolve a user-provided CamVid path to the real RGB/label dir.

    Accepts the exact dir, a parent (camvid root or the archive's CamVid-main),
    or blank. Returns None only when nothing usable is found so the caller can
    surface a clear error instead of a raw FileNotFoundError.
    """
    text = (path_text or "").strip()
    if not text:
        return None
    base = Path(text).expanduser()
    if not base.exists():
        return None
    found = _find_dataset_dir(base, name)
    if found is not None:
        return found
    return base if base.is_dir() else None


def _extract_zip_flat(zip_path: Path, output_dir: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(output_dir)
    # GitHub archives normally extract into a single top-level folder. Move the
    # contents up so users see a stable path regardless of branch hash/name.
    # The zip is downloaded into this same directory, so exclude it (and any
    # other stray files) when checking for that single top-level folder;
    # otherwise the flatten is silently skipped and paths stay nested.
    dir_children = [path for path in output_dir.iterdir() if path.is_dir()]
    stray_files = [path for path in output_dir.iterdir() if path.is_file() and path.resolve() != zip_path.resolve()]
    if len(dir_children) == 1 and not stray_files:
        top = dir_children[0]
        for child in top.iterdir():
            target = output_dir / child.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            child.rename(target)
        top.rmdir()


def _find_dataset_dir(root: Path, name: str) -> Path | None:
    """Locate a dataset subdirectory by name, tolerant of archive nesting.

    GitHub archive branch renames (master -> main) and residual nesting mean the
    directory may not sit directly under root. Prefer the direct path, then fall
    back to a recursive search so a successful download is not reported as a
    failure just because of an extra folder level.
    """
    direct = root / name
    if direct.is_dir():
        return direct
    for candidate in sorted(root.rglob(name)):
        if candidate.is_dir():
            return candidate
    return None


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
    # GitHub renamed the default branch to `main`; codeload no longer serves
    # `master`. Try `main` first, then fall back to `master` for older mirrors.
    urls = [
        "https://github.com/lih627/CamVid/archive/refs/heads/main.zip",
        "https://github.com/lih627/CamVid/archive/refs/heads/master.zip",
    ]
    if _find_dataset_dir(root, "CamVid_RGB") is None or _find_dataset_dir(root, "CamVid_Label") is None:
        last_error: Exception | None = None
        downloaded = False
        for url in urls:
            try:
                _download_url_to_file(url, zip_path, timeout_seconds=30)
                _extract_zip_flat(zip_path, root)
                downloaded = True
                break
            except Exception as exc:  # pragma: no cover - network failures are environment-specific.
                last_error = exc
            finally:
                zip_path.unlink(missing_ok=True)
        if not downloaded:
            raise HTTPException(status_code=502, detail=f"download_failed: {last_error}")
    images_dir = _find_dataset_dir(root, "CamVid_RGB")
    labels_dir = _find_dataset_dir(root, "CamVid_Label")
    if images_dir is None or labels_dir is None:
        raise HTTPException(
            status_code=502,
            detail=(
                f"camvid_layout_unexpected: 下载已完成，但在 {root} 下未找到 CamVid_RGB / CamVid_Label 目录。"
                "请检查下载的压缩包结构，或手动指定目录。"
            ),
        )
    output_path = Path(output or "docs/datasets/camvid-manifest.jsonl").expanduser()
    try:
        rows = create_camvid_manifest(
            images_dir=images_dir,
            labels_dir=labels_dir,
            output_path=output_path,
            split="road",
            limit=limit,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"camvid_manifest_failed: {exc}") from exc
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
def dataset_create_open(dataset: str, images: str = "", labels: str = "", output: str = "", split: str = "road", limit: int = 0, as_json: bool = False):
    if dataset not in {"bdd100k_drivable", "camvid"}:
        raise HTTPException(status_code=400, detail="unsupported_open_dataset")
    default_output = "docs/datasets/camvid-manifest.jsonl" if dataset == "camvid" else "docs/datasets/bdd100k-drivable-manifest.jsonl"
    output_path = Path(output or default_output).expanduser()
    output_parent = output_path.parent.resolve()
    if not any(root == output_parent or root in output_parent.parents for root in _allowed_local_roots()):
        raise HTTPException(status_code=403, detail=f"output_not_allowed: {output_path}")

    if dataset == "camvid":
        # Blank fields → use the already-downloaded CamVid. A parent dir (camvid
        # root or the archive's CamVid-main) → resolve the real RGB/Label dirs.
        detected_images, detected_labels = _detect_camvid_dirs()
        images_dir = _resolve_camvid_subdir(images, "CamVid_RGB") or detected_images
        labels_dir = _resolve_camvid_subdir(labels, "CamVid_Label") or detected_labels
        if images_dir is None or labels_dir is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "camvid_not_found: 未找到 CamVid_RGB / CamVid_Label 目录。"
                    "请先在第 1 步一键下载 CamVid，或在第 2 步填入包含这两个目录的路径。"
                ),
            )
        for path in [images_dir, labels_dir]:
            resolved = path.resolve()
            if not any(root == resolved or root in resolved.parents for root in _allowed_local_roots()):
                raise HTTPException(status_code=403, detail=f"path_not_allowed: {path}")
        try:
            rows = create_camvid_manifest(images_dir=images_dir, labels_dir=labels_dir, output_path=output_path, split=split.strip() or "road", limit=limit)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"camvid_manifest_failed: {exc}") from exc
    else:
        if not images.strip() or not labels.strip():
            raise HTTPException(status_code=400, detail="missing_images_or_labels")
        images_dir = Path(images).expanduser()
        labels_path = Path(labels).expanduser()
        for path in [images_dir, labels_path]:
            resolved = path.resolve()
            if not any(root == resolved or root in resolved.parents for root in _allowed_local_roots()):
                raise HTTPException(status_code=403, detail=f"path_not_allowed: {path}")
        try:
            rows = create_bdd100k_drivable_manifest(
                images_dir=images_dir,
                labels_path=labels_path,
                output_path=output_path,
                split=split.strip() or "road",
                limit=limit,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"bdd100k_manifest_failed: {exc}") from exc

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


MANIFEST_BROWSE_PAGE_SIZE = 24


@router.get("/datasets/manifest/ui", response_class=HTMLResponse)
def dataset_manifest_ui(manifest: str, page: int = 1):
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")
    rows = load_jsonl(manifest_path)
    total = len(rows)
    total_pages = max(1, (total + MANIFEST_BROWSE_PAGE_SIZE - 1) // MANIFEST_BROWSE_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * MANIFEST_BROWSE_PAGE_SIZE
    page_rows = rows[start : start + MANIFEST_BROWSE_PAGE_SIZE]
    manifest_q = html.escape(str(manifest_path))
    cards = []
    for row in page_rows:
        frame_id = html.escape(str(row.get("frame_id", "")))
        image_path = str(row.get("image_path") or "")
        mask_path = str(row.get("mask_path") or "")
        image_html = "<p class='muted'>无可预览图片路径</p>"
        if image_path:
            # Lazy-load a downscaled thumbnail; click opens the full-size image.
            thumb = f"/diagnostics/local-file?path={html.escape(image_path)}&w=480"
            full = f"/diagnostics/local-file?path={html.escape(image_path)}"
            image_html = f"<a href='{full}' target='_blank'><img loading='lazy' decoding='async' src='{thumb}' alt='{frame_id}'></a>"
        mask_html = ""
        if mask_path:
            mask_thumb = f"/diagnostics/local-file?path={html.escape(mask_path)}&w=480"
            mask_full = f"/diagnostics/local-file?path={html.escape(mask_path)}"
            mask_html = f"<div><h3>Mask</h3><a href='{mask_full}' target='_blank'><img loading='lazy' decoding='async' src='{mask_thumb}' alt='mask {frame_id}'></a></div>"
        gt_raw = row.get("ground_truth", {})
        pred_raw = row.get("prediction", row.get("path_guidance", {}))
        gt = html.escape(json.dumps(gt_raw, ensure_ascii=False, indent=2))
        pred = html.escape(json.dumps(pred_raw, ensure_ascii=False, indent=2))
        coverage = html.escape(json.dumps(row.get("mask_coverage", {}), ensure_ascii=False, indent=2))
        cards.append(
            f"""<div class='card'><h2>{frame_id}</h2><div class='row'><div>{image_html}</div>{mask_html}<div><h3>真实答案 Ground Truth</h3><p class='hint'>由 mask 或人工标注生成，表示这一帧真实的通行状态。</p><pre>{gt}</pre><h3>Mask 覆盖率</h3><p class='hint'>每个区域中白色/可通行像素比例。</p><pre>{coverage}</pre><h3>VQASee 预测 Prediction</h3><p class='hint'>模型/算法输出。若为空，说明还没对该 manifest 跑 prediction。</p><pre>{pred}</pre></div></div></div>"""
        )

    nav_bits = [f"<span class='muted'>共 {total} 帧 · 第 {page}/{total_pages} 页</span>"]
    if page > 1:
        nav_bits.append(f"<a href='/diagnostics/datasets/manifest/ui?manifest={manifest_q}&page={page - 1}'>← 上一页</a>")
    if page < total_pages:
        nav_bits.append(f"<a href='/diagnostics/datasets/manifest/ui?manifest={manifest_q}&page={page + 1}'>下一页 →</a>")
    nav = "<p class='hint'>" + " · ".join(nav_bits) + "</p>"

    body = (
        f"<p><a href='/diagnostics/datasets/ui'>← 返回数据集评估</a> · <a href='/diagnostics/datasets/evaluate/ui?manifest={manifest_q}'>评估此 manifest</a></p>"
        f"<h1>Manifest 浏览：{html.escape(manifest_path.name)}</h1>"
        + nav
        + ("".join(cards) or "<p>manifest 为空。</p>")
        + (nav if cards else "")
    )
    return _html_page("Manifest 浏览", body)


@router.get("/datasets/evaluate")
def dataset_evaluate(manifest: str) -> dict:
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")
    return evaluate_path_guidance(load_jsonl(manifest_path))


@router.post("/datasets/predict")
def dataset_predict(manifest: str, model: str = "", write_back: bool = False, limit: int = 0) -> dict:
    """Run the offline traversability predictor over a manifest.

    Returns the predictor capability explicitly. When ``capability`` is not
    ``active`` (no onnxruntime / no model), the response says so rather than
    writing empty predictions. When active and ``write_back`` is set, predictions
    are merged into the manifest by ``frame_id`` so the evaluate/browse pages can
    reflect them.
    """
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")
    rows = load_jsonl(manifest_path)
    predictor = TraversabilityPredictor(model_path=model.strip() or None)
    result = predict_manifest(rows, predictor, limit=limit)
    capability = result["capability"]
    response = {
        "status": "ok" if capability.get("capability") == "active" else "unsupported",
        "capability": capability.get("capability"),
        "reason": capability.get("reason"),
        "predicted": result["predicted"],
        "errors": result["errors"][:20],
        "error_count": len(result["errors"]),
    }
    if capability.get("capability") != "active":
        return response
    if write_back:
        output_parent = manifest_path.parent.resolve()
        if not any(root == output_parent or root in output_parent.parents for root in _allowed_local_roots()):
            raise HTTPException(status_code=403, detail=f"manifest_not_writable: {manifest_path}")
        predictions_by_frame = {row["frame_id"]: row["prediction"] for row in result["predictions"]}
        for row in rows:
            frame_id = str(row.get("frame_id") or row.get("frame") or row.get("image") or "")
            if frame_id in predictions_by_frame:
                row["prediction"] = predictions_by_frame[frame_id]
        manifest_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )
        response["written_back"] = True
    return response


@router.post("/datasets/baseline")
def dataset_baseline(manifest: str, name: str = "") -> dict:
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")
    report = evaluate_path_guidance(load_jsonl(manifest_path))
    baseline_name = name.strip() or manifest_path.stem
    baseline_path = save_baseline(baseline_name, report, source=f"manifest:{manifest_path.name}")
    return {"status": "ok", "baseline": baseline_name, "baseline_path": str(baseline_path), "report": report}


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
    missing_card = _missing_prediction_card(report.get("missing_prediction_count"), report.get("labeled_frames"))
    recs = "".join(f"<li>{html.escape(str(item))}</li>" for item in report.get("recommendations", []))
    encoded = html.escape(manifest)
    script = f"""<script>
async function runPredict() {{
  const status = document.getElementById('predictStatus');
  const model = document.getElementById('predictModel').value.trim();
  status.style.display = 'block';
  status.className = 'status';
  status.textContent = '正在对该 manifest 运行预测…（首次可能较慢）';
  try {{
    let url = '/diagnostics/datasets/predict?manifest={encoded}&write_back=true';
    if (model) url += '&model=' + encodeURIComponent(model);
    const resp = await fetch(url, {{method: 'POST'}});
    const payload = await resp.json();
    if (!resp.ok) {{ status.className = 'status error'; status.textContent = '预测失败：' + (payload.detail || resp.statusText); return; }}
    if (payload.capability !== 'active') {{
      status.className = 'status error';
      status.innerHTML = '预测器不可用（capability=' + payload.capability + '）：' + (payload.reason || '') + '<br><span class="muted">这是明确告知，不是静默跳过。请先安装 onnxruntime 并提供通行性分割模型。</span>';
      return;
    }}
    status.className = 'status ok';
    status.innerHTML = '已对 ' + payload.predicted + ' 帧写入预测。<a href="/diagnostics/datasets/evaluate/ui?manifest={encoded}">刷新评估</a>';
  }} catch (error) {{ status.className = 'status error'; status.textContent = '请求失败：' + error; }}
}}
async function saveBaseline() {{
  const status = document.getElementById('baselineStatus');
  status.style.display = 'block';
  status.className = 'status';
  status.textContent = '正在保存基线…';
  try {{
    const resp = await fetch('/diagnostics/datasets/baseline?manifest={encoded}', {{method: 'POST'}});
    const payload = await resp.json();
    if (!resp.ok) {{ status.className = 'status error'; status.textContent = '保存失败：' + (payload.detail || resp.statusText); return; }}
    status.className = 'status ok';
    status.innerHTML = '已保存基线 <b>' + payload.baseline + '</b>。';
  }} catch (error) {{ status.className = 'status error'; status.textContent = '请求失败：' + error; }}
}}
</script>"""
    predict_card = f"""
<div class='card'>
  <h2><span class='step'>预测</span>运行预测（补全 prediction）</h2>
  <p class='hint'>开源数据集只有真实答案、没有预测。点这里用服务端通行性预测器给每帧生成预测，指标才有意义。</p>
  <button onclick='runPredict()'>对该 manifest 运行预测</button>
  <details><summary>高级设置</summary><p><label>通行性分割模型路径（留空用默认 / 环境变量）<br><input id='predictModel' class='wide' placeholder='留空即可'></label></p></details>
  <div id='predictStatus' class='status' style='display:none'></div>
</div>
"""
    baseline_card = """
<div class='card'>
  <h2>存为回归基线</h2>
  <p class='hint'>把当前指标存下来，作为以后回归对比的已知良好点。</p>
  <button class='secondary' onclick='saveBaseline()'>存为基线</button>
  <div id='baselineStatus' class='status' style='display:none'></div>
</div>
"""
    body = f"""
<p><a href='/diagnostics/datasets/ui'>← 返回数据集评估</a> · <a href='/diagnostics/datasets/manifest/ui?manifest={html.escape(manifest)}'>浏览 manifest</a> · <a href='/diagnostics/datasets/ios-harness/ui?manifest={html.escape(manifest)}'>iPhone 真身评估</a></p>
<h1>数据集评估：{html.escape(Path(manifest).name)}</h1>
{script}
{missing_card}
{predict_card}
{cards}
{baseline_card}
<div class='card'><h2>建议</h2><ul>{recs}</ul></div>
<details><summary>完整 JSON 报告</summary><pre>{details}</pre></details>
"""
    return _html_page("数据集评估报告", body)


@router.post("/datasets/ios-harness/parity")
def dataset_ios_harness_parity(manifest: str, predictions: str, threshold: float = 0.20) -> dict:
    """Compare the iPhone offline harness predictions vs the server ONNX proxy.

    Honesty: if the server proxy predictor is unavailable (no onnxruntime / no
    model) this returns an explicit ``unsupported`` reason rather than a fake
    agreement number.
    """
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")
    pred_path = _safe_local_file(predictions)
    manifest_rows = load_jsonl(manifest_path)
    ios_rows = load_jsonl(pred_path)
    predictor = TraversabilityPredictor()
    server_result = predict_manifest(manifest_rows, predictor)
    capability = server_result["capability"]
    if capability.get("capability") != "active":
        return {
            "status": "unsupported",
            "capability": capability.get("capability"),
            "reason": capability.get("reason"),
        }
    parity = compute_parity(ios_rows, server_result["predictions"], drift_threshold=threshold)
    return {"status": "ok", **parity}


@router.get("/datasets/ios-harness/ui", response_class=HTMLResponse)
def dataset_ios_harness_ui(manifest: str, predictions: str = ""):
    """Wizard: evaluate the REAL iPhone on-device perception stack on a dataset.

    The server cannot build/run the Swift+Core ML harness itself, so this page
    walks the user through producing harness predictions on a Mac, then scores
    them against the manifest ground truth (and optionally parity vs the server
    proxy). No silent failure: bad paths and unavailable proxies are shown.
    """
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")
    encoded_manifest = html.escape(manifest)
    default_out = f"/tmp/{manifest_path.stem}-ios-harness.jsonl"
    run_cmd = (
        "ios-vqa-app/perception-harness/.build/debug/PerceptionHarness \\\n"
        f"  --manifest {manifest} \\\n"
        f"  --out {default_out}"
    )
    steps = f"""
<div class='card'>
  <h2><span class='step'>1</span>在 Mac 上跑真身感知</h2>
  <p class='hint'>平台跑的是 iPhone 上一模一样的感知代码（YOLO11n Core ML + 通行区域引擎），不是近似实现。需在装有 App 的 Core ML 模型的 Mac 上执行一次：</p>
  <pre>cd ios-vqa-app/perception-harness &amp;&amp; swift build</pre>
  <pre>{html.escape(run_cmd)}</pre>
  <p class='muted'>说明：离线环境没有 LiDAR/ARKit 深度，这反映 iPhone 的“仅相机”分支；每行结果都会标注 depth_capability，不隐藏这一点。</p>
</div>
<div class='card'>
  <h2><span class='step'>2</span>把结果喂回平台评估</h2>
  <p class='hint'>粘贴上一步生成的预测文件路径，用数据集真实答案给 iPhone 真身打分。</p>
  <form method='get' action='/diagnostics/datasets/ios-harness/ui'>
    <input type='hidden' name='manifest' value='{encoded_manifest}'>
    <label>预测文件路径（harness 的 --out）<br>
      <input class='wide' name='predictions' value='{html.escape(predictions or default_out)}' placeholder='{html.escape(default_out)}'></label>
    <p><button type='submit'>评估 iPhone 真身</button></p>
  </form>
</div>
"""
    header = (
        f"<p><a href='/diagnostics/datasets/ui'>← 返回数据集评估</a> · "
        f"<a href='/diagnostics/datasets/manifest/ui?manifest={encoded_manifest}'>浏览 manifest</a> · "
        f"<a href='/diagnostics/datasets/evaluate/ui?manifest={encoded_manifest}'>服务器代理评估</a></p>"
        f"<h1>用 iPhone 真身评估：{html.escape(manifest_path.name)}</h1>"
    )

    if not predictions.strip():
        return _html_page("iPhone 真身评估", header + steps)

    pred_path = Path(predictions).expanduser()
    if not pred_path.is_file():
        err = (
            f"<div class='status error'>找不到预测文件：{html.escape(str(pred_path))}<br>"
            "<span class='muted'>请先完成第 1 步生成该文件（这是明确报错，不是静默跳过）。</span></div>"
        )
        return _html_page("iPhone 真身评估", header + err + steps)

    report = evaluate_path_guidance(load_jsonl(manifest_path), load_jsonl(pred_path))

    def card(title: str, value: object, hint: str = "") -> str:
        return (
            f"<div class='card'><h2>{html.escape(title)}</h2>"
            f"<p style='font-size:2rem;font-weight:800'>{html.escape(str(value))}</p>"
            f"<p class='muted'>{html.escape(hint)}</p></div>"
        )

    cards = "<div class='grid'>" + "".join([
        card("有标注帧", report.get("labeled_frames"), "参与打分的帧数"),
        card("状态准确率", report.get("status_accuracy"), "近处/左/右三区域状态匹配率"),
        card("方向准确率", report.get("focus_direction_accuracy"), "关注方向是否匹配"),
        card("漏报风险", report.get("risk_miss_count"), "真实 caution/blocked 却报 candidateOpen（最危险）"),
        card("误阻挡", report.get("false_block_count"), "真实可走却报占用"),
        card("Unknown 比例", report.get("unknown_prediction_rate"), "预测为信息不足的比例"),
    ]) + "</div>"

    encoded_pred = html.escape(str(pred_path))
    parity_script = f"""<script>
async function runParity() {{
  const status = document.getElementById('parityStatus');
  status.style.display = 'block';
  status.className = 'status';
  status.textContent = '正在用服务器 ONNX 代理做一致性对比…（首次可能较慢）';
  try {{
    const url = '/diagnostics/datasets/ios-harness/parity?manifest={encoded_manifest}&predictions=' + encodeURIComponent('{encoded_pred}');
    const resp = await fetch(url, {{method: 'POST'}});
    const payload = await resp.json();
    if (!resp.ok) {{ status.className = 'status error'; status.textContent = '对比失败：' + (payload.detail || resp.statusText); return; }}
    if (payload.status !== 'ok') {{
      status.className = 'status error';
      status.innerHTML = '服务器代理不可用（capability=' + payload.capability + '）：' + (payload.reason || '') + '<br><span class="muted">明确告知，非静默跳过。装 onnxruntime + 分割模型后可对比。</span>';
      return;
    }}
    status.className = 'status ' + (payload.drift_alert ? 'error' : 'ok');
    status.innerHTML = '总体一致率 ' + (payload.overall_agreement ?? '?') + '，漂移率 ' + (payload.drift_rate ?? '?')
      + (payload.drift_alert ? '（超过阈值，iPhone 与服务器代理分歧较大）' : '（在阈值内）');
  }} catch (error) {{ status.className = 'status error'; status.textContent = '请求失败：' + error; }}
}}
</script>"""
    parity_card = """
<div class='card'>
  <h2>一致性对比（iPhone 真身 vs 服务器代理）</h2>
  <p class='hint'>两套是独立的预测器（iPhone 用 YOLO+启发式，服务器用分割 ONNX）。这是“对比找分歧”，不是要求二者数值对齐。</p>
  <button class='secondary' onclick='runParity()'>运行一致性对比</button>
  <div id='parityStatus' class='status' style='display:none'></div>
</div>
"""
    details = html.escape(json.dumps(report, ensure_ascii=False, indent=2))
    recs = "".join(f"<li>{html.escape(str(item))}</li>" for item in report.get("recommendations", []))
    body = (
        header
        + parity_script
        + f"<div class='status ok'>已用 {html.escape(str(pred_path.name))} 对 iPhone 真身打分（prediction_source=ios_coreml_offline_harness）。</div>"
        + cards
        + parity_card
        + f"<div class='card'><h2>建议</h2><ul>{recs}</ul></div>"
        + f"<details><summary>完整 JSON 报告</summary><pre>{details}</pre></details>"
        + steps
    )
    return _html_page("iPhone 真身评估", body)


@router.get("/perception-config")
def perception_config_get() -> dict:
    """Return the active perception config (same payload as /runtime/perception-config)."""
    try:
        return load_active_config().to_dict()
    except ConfigValidationError as exc:
        raise HTTPException(status_code=500, detail=f"perception_config_invalid: {exc}") from exc


@router.post("/perception-config/bump")
def perception_config_bump(updates: dict = Body(default_factory=dict)) -> dict:
    """Apply partial ROI/threshold updates, bump the version, persist.

    Rejects invalid values (out of range / bad ROI) with a 400 and writes
    nothing, so a bad edit can never be shipped to devices.
    """
    try:
        new_config = bump_and_save(updates or {})
    except ConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=f"invalid_config: {exc}") from exc
    return {"status": "ok", "config": new_config.to_dict(), "store": str(config_store_path())}


@router.get("/perception-config/ui", response_class=HTMLResponse)
def perception_config_ui():
    config = load_active_config().to_dict()
    roi = config["roi"]
    thr = config["thresholds"]

    def num(name: str, value: float, label: str, hint: str = "") -> str:
        return (
            f"<label class='num'>{html.escape(label)}"
            f"<input type='number' step='0.01' min='0' max='1' id='{name}' value='{value}'>"
            f"<span class='muted'>{html.escape(hint)}</span></label>"
        )

    roi_block = ""
    for region, cn in (("near", "近处正前"), ("left", "左前"), ("right", "右前")):
        r = roi[region]
        roi_block += (
            f"<div class='card'><h2>{cn} ROI</h2><div class='row'>"
            + num(f"{region}_x", r["x"], "x")
            + num(f"{region}_y", r["y"], "y")
            + num(f"{region}_w", r["w"], "w")
            + num(f"{region}_h", r["h"], "h")
            + "</div></div>"
        )

    thr_block = (
        "<div class='card'><h2>阈值</h2><div class='row'>"
        + num("near_blocked_area", thr["near_blocked_area"], "近处判定占用置信", "越高越不容易报占用")
        + num("side_blocked_area", thr["side_blocked_area"], "侧向判定占用置信")
        + num("seg_near_caution_ratio", thr["seg_near_caution_ratio"], "近处可走比例下限")
        + num("seg_side_caution_ratio", thr["seg_side_caution_ratio"], "侧向可走比例下限")
        + num("seg_traversable_pixel", thr["seg_traversable_pixel"], "分割可走像素阈值")
        + "</div></div>"
    )

    script = """<script>
function val(id){return parseFloat(document.getElementById(id).value);}
async function saveConfig(){
  const status = document.getElementById('cfgStatus');
  status.style.display='block'; status.className='status'; status.textContent='正在校验并升级版本…';
  const updates = {
    roi: {
      near:{x:val('near_x'),y:val('near_y'),w:val('near_w'),h:val('near_h')},
      left:{x:val('left_x'),y:val('left_y'),w:val('left_w'),h:val('left_h')},
      right:{x:val('right_x'),y:val('right_y'),w:val('right_w'),h:val('right_h')}
    },
    thresholds:{
      near_blocked_area:val('near_blocked_area'),
      side_blocked_area:val('side_blocked_area'),
      seg_near_caution_ratio:val('seg_near_caution_ratio'),
      seg_side_caution_ratio:val('seg_side_caution_ratio'),
      seg_traversable_pixel:val('seg_traversable_pixel')
    }
  };
  try{
    const resp = await fetch('/diagnostics/perception-config/bump',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(updates)});
    const payload = await resp.json();
    if(!resp.ok){status.className='status error'; status.textContent='保存失败：'+(payload.detail||resp.statusText); return;}
    status.className='status ok';
    status.innerHTML='已保存并升级到版本 <b>v'+payload.config.version+'</b>（hash '+payload.config.hash+'）。iPhone 下次连接会拉取此版本。';
  }catch(e){status.className='status error'; status.textContent='请求失败：'+e;}
}
</script>"""

    body = (
        "<p><a href='/diagnostics/ui'>← 返回平台首页</a></p>"
        f"<h1>感知配置（当前 v{config['version']}）</h1>"
        "<p class='hint'>这些数值控制 iPhone 端“近处/左/右”通行判定的 ROI 与阈值。默认值等于 App 内置常量。"
        "先在 iPhone 真身评估里验证候选参数，再回到这里保存并升级版本，iPhone 下次连接自动生效。</p>"
        + script
        + roi_block
        + thr_block
        + "<div class='card'><button onclick='saveConfig()'>保存并升级版本</button>"
        "<div id='cfgStatus' class='status' style='display:none'></div></div>"
        + f"<p class='muted'>存储位置：{html.escape(str(config_store_path()))}</p>"
    )
    return _html_page("感知配置", body)


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
