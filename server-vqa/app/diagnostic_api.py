"""HTTP API for local diagnostic capture management."""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from PIL import Image
from pydantic import BaseModel

from app.case_store import (
    annotate as case_annotate,
    cluster_failures,
    dataset_key_from_manifest,
    failure_label as case_failure_label,
    frame_failure_types,
    list_cases,
    load_case,
    set_status as case_set_status,
    status_label as case_status_label,
    upsert_clusters,
    CASE_STATUSES,
)
from app.diagnostic_capture import capture_root, get_session_dir, list_sessions
from app.diagnostic_report import generate_report_from_session_dir
from app.eval_baseline import list_baselines, load_baseline, save_baseline
from app.open_dataset_adapters import create_bdd100k_drivable_manifest, create_camvid_manifest
from app.guidance_path import GuidancePath, GuidancePathError
from app.guidance_path_eval import evaluate_guidance_paths
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
    table {{ border-collapse: collapse; margin: 8px 0; }}
    th, td {{ border: 1px solid #3a3a3c; padding: 6px 10px; text-align: left; font-size: 0.92rem; }}
    th {{ color: #a1a1a6; font-weight: 600; }}
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
  <div class='card'><h2>5. 闭环 case</h2><p class='muted'>评估里的失败帧自动聚类成可跟踪、能重开的 case（借鉴 DCL 统一载体 + 生命周期）。同一问题发生两次会自动重开。</p><p><a href='/diagnostics/cases/ui'>打开 case 列表</a></p></div>
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
    default_out = str(_harness_out_path(manifest_path))
    cache = _harness_cache_info(manifest_path)
    run_cmd = (
        "ios-vqa-app/perception-harness/.build/debug/PerceptionHarness \\\n"
        f"  --manifest {manifest} \\\n"
        f"  --out {default_out}"
    )
    run_script = f"""<script>
async function runHarness(force) {{
  const status = document.getElementById('runStatus');
  const btn = document.getElementById('runBtn');
  const btn2 = document.getElementById('rerunBtn');
  status.style.display = 'block';
  status.className = 'status';
  status.textContent = force ? '正在强制重跑真身感知…（约 10–30 秒）' : '正在处理…（若已有结果会秒回，否则跑真身约 10–30 秒）';
  if (btn) btn.disabled = true;
  if (btn2) btn2.disabled = true;
  try {{
    const url = '/diagnostics/datasets/ios-harness/run?manifest={encoded_manifest}' + (force ? '&force=true' : '');
    const resp = await fetch(url, {{method: 'POST'}});
    const payload = await resp.json();
    if (!resp.ok) {{ status.className = 'status error'; status.textContent = '触发失败：' + (payload.detail || resp.statusText); if(btn)btn.disabled=false; if(btn2)btn2.disabled=false; return; }}
    if (payload.status === 'ok' || payload.status === 'cached') {{
      status.className = 'status ok';
      status.textContent = (payload.note || '完成') + ' 正在打开评估结果…';
      const next = '/diagnostics/datasets/ios-harness/ui?manifest={encoded_manifest}&predictions=' + encodeURIComponent(payload.predictions);
      window.location.href = next;
      return;
    }}
    status.className = 'status error';
    let msg = payload.reason || ('状态：' + payload.status);
    if (payload.stderr) {{ msg += '\\n\\nstderr:\\n' + payload.stderr; }}
    if (payload.build_stderr) {{ msg += '\\n\\n编译报错:\\n' + payload.build_stderr; }}
    status.innerHTML = '<pre style="margin:0;white-space:pre-wrap">' + msg.replace(/</g,'&lt;') + '</pre><p class="muted">可改用下方手动步骤。</p>';
    if(btn)btn.disabled=false; if(btn2)btn2.disabled=false;
  }} catch (error) {{ status.className = 'status error'; status.textContent = '请求失败：' + error; if(btn)btn.disabled=false; if(btn2)btn2.disabled=false; }}
}}
</script>"""

    if cache.get("exists"):
        eval_url = (
            f"/diagnostics/datasets/ios-harness/ui?manifest={encoded_manifest}"
            f"&predictions={html.escape(cache['out_path'])}"
        )
        cfg_v = cache.get("config_version")
        basis = "按内容指纹判定" if cache.get("fingerprint") == "content" else "按时间近似判定"
        summary = (
            f"已有上次结果：<b>{cache.get('count', 0)}</b> 帧，生成于 "
            f"{html.escape(str(cache.get('generated_at', '?')))}"
            + (f"，配置 v{cfg_v}" if cfg_v is not None else "")
            + f"（{basis}）"
        )
        if cache.get("fresh"):
            cache_state = (
                "<div class='status ok'>结果仍然新鲜（数据集/感知代码/配置都没变）——"
                "<b>不用重跑</b>，直接评估即可。</div>"
            )
        else:
            reasons = "".join(f"<li>{html.escape(r)}</li>" for r in cache.get("stale_reasons", []))
            cache_state = (
                f"<div class='status error'>结果可能已过期，建议重跑：<ul>{reasons}</ul></div>"
            )
        cache_card = f"""
<div class='card'>
  <h2><span class='step'>1</span>用真身结果（已有缓存）</h2>
  <p class='hint'>{summary}</p>
  {cache_state}
  <p>
    <a href='{eval_url}'><button type='button'>直接查看评估（用缓存）</button></a>
    <button id='rerunBtn' class='secondary' onclick='runHarness(true)'>↻ 用当前配置重新跑</button>
  </p>
  <div id='runStatus' class='status' style='display:none'></div>
  <details><summary>什么时候需要重跑？</summary>
    <p class='hint'>换了数据集、重新编译了感知代码/模型、或在“Perception Config (OTA)”里改了 ROI/阈值并升级了版本时才需要重跑；否则复用缓存即可。重跑会用<b>当前生效配置</b>，所以调完参数重跑才能看到变化。</p>
  </details>
</div>"""
        run_button_block = ""
    else:
        cache_card = ""
        run_button_block = f"""
<div class='card'>
  <h2><span class='step'>1</span>跑真身感知</h2>
  <p class='hint'>平台跑的是 iPhone 上一模一样的感知代码（YOLO11n Core ML + 通行区域引擎），不是近似实现。诊断台就在这台 Mac 上，可直接一键触发，无需自己开终端。跑一次后会缓存，之后无需每次重跑。</p>
  <div class='callout'>
    <p><b>推荐：一键在本机跑</b>（服务器直接调用已编译的 harness；首次会自动编译）</p>
    <button id='runBtn' onclick='runHarness(false)'>▶ 一键在本机跑真身感知</button>
    <div id='runStatus' class='status' style='display:none'></div>
    <p class='muted'>仅在诊断台运行于 macOS 时可用；非 Mac 或缺 Core ML 模型会明确报错，不会静默假装成功。</p>
  </div>
  <details><summary>或手动执行（等价命令）</summary>
    <pre>cd ios-vqa-app/perception-harness &amp;&amp; swift build</pre>
    <pre>{html.escape(run_cmd)}</pre>
  </details>
  <p class='muted'>说明：离线环境没有 LiDAR/ARKit 深度，这反映 iPhone 的“仅相机”分支；每行结果都会标注 depth_capability，不隐藏这一点。</p>
</div>"""
    steps = f"""
{run_script}
{cache_card}
{run_button_block}
<div class='card'>
  <h2><span class='step'>2</span>把结果喂回平台评估</h2>
  <p class='hint'>一键跑完会自动带着预测路径进入评估。也可手动粘贴上一步生成的预测文件路径。</p>
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

    manifest_rows = load_jsonl(manifest_path)
    prediction_rows = load_jsonl(pred_path)
    report = evaluate_path_guidance(manifest_rows, prediction_rows)

    # Line-level guidance report — this is what the centerline algorithm actually
    # moves. The region metrics below are three-zone status and are INDEPENDENT of
    # the guidance line, so surfacing only region metrics hid every line improvement.
    guidance_pairs, guidance_skipped = _guidance_pairs(manifest_rows, prediction_rows)
    guidance_report = evaluate_guidance_paths(guidance_pairs) if guidance_pairs else None

    def card(title: str, value: object, hint: str = "") -> str:
        return (
            f"<div class='card'><h2>{html.escape(title)}</h2>"
            f"<p style='font-size:2rem;font-weight:800'>{html.escape(str(value))}</p>"
            f"<p class='muted'>{html.escape(hint)}</p></div>"
        )

    def num(value: object, digits: int = 3) -> str:
        if isinstance(value, bool) or value is None:
            return str(value)
        if isinstance(value, (int,)):
            return str(value)
        if isinstance(value, float):
            return f"{value:.{digits}f}"
        return str(value)

    if guidance_report is not None:
        g = guidance_report
        guidance_cards = (
            "<div class='callout'><h2>引导线指标（中心线算法影响的就是这一组）</h2>"
            "<p class='hint'>这一组衡量「可通行引导线」本身：能不能画出线、画得准不准、"
            "会不会在真值无路处硬画。下面的三区状态指标与引导线无关，所以中心线改动不会动它们。</p>"
            "<div class='grid'>"
            + "".join([
                card("有线可比帧 both_ok", g.get("both_ok"), f"真值与预测都成线的帧（共 {g.get('frames')} 帧）"),
                card("漏线 missed_path", g.get("missed_path_frames"), "真值有路、预测却没画出线（越低越好）"),
                card("虚报路 false_go", g.get("false_go_frames"), "真值无路、预测却宣称有路（安全红线，须为 0）"),
                card("落廊率 hit_rate", num(g.get("hit_rate")), "预测线落在真值走廊内的比例（越高越好）"),
                card("横向偏差 mean_deviation", num(g.get("mean_deviation")), "与真值线的平均横向误差（越低越好）"),
                card("越界 over_extension", num(g.get("over_extension")), "预测线尾越过真值自由空间的比例（越低越安全）"),
            ])
            + "</div>"
            + (f"<p class='muted'>另有 {guidance_skipped} 帧因线数据不合法未计入（明确暴露，非静默丢弃）。</p>" if guidance_skipped else "")
            + "</div>"
        )
    else:
        guidance_cards = (
            "<div class='callout'><h2>引导线指标</h2>"
            "<p class='muted'>此 manifest 缺少 ground_truth_path，或预测缺少 guidance_path，"
            "无法做线级评估。重生成带真值线的 manifest 并重跑真身感知后即可显示。</p></div>"
        )

    cards = guidance_cards + "<h2 style='margin-top:1.5rem'>三区状态指标（region，与引导线独立）</h2><div class='grid'>" + "".join([
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
  <h2>一致性对比（可选）：iPhone 真身 vs 服务器代理</h2>
  <p class='hint'><b>作用</b>：拿一套<b>独立</b>的服务器端预测器（分割 ONNX）复算同样的帧，
  和 iPhone 真身（YOLO+启发式）逐帧比对，用来<b>发现两套预测器何时分歧变大（漂移）</b>——
  一种「用第二个裁判交叉验证」的手段。它<b>不参与</b>上面的准确率/引导线打分，
  也<b>不是评估通过的前提</b>。</p>
  <p class='hint'>没装 ONNX 依赖时它会明确报 <code>unsupported</code>（而非静默跳过）；
  你现在能看到上面的指标，说明真身评估本身是好的。要启用交叉验证：
  <code>pip install onnxruntime</code> + 提供分割模型。</p>
  <button class='secondary' onclick='runParity()'>运行一致性对比</button>
  <div id='parityStatus' class='status' style='display:none'></div>
</div>
"""
    details = html.escape(json.dumps(report, ensure_ascii=False, indent=2))
    recs = "".join(f"<li>{html.escape(str(item))}</li>" for item in report.get("recommendations", []))
    frames_url = (
        f"/diagnostics/datasets/ios-harness/frames/ui?manifest={encoded_manifest}"
        f"&predictions={html.escape(str(pred_path))}"
    )
    frames_callout = (
        f"<div class='callout'><h2>看图：iPhone 感知层在每张图上识别成了什么</h2>"
        f"<p class='hint'>光看数字不够。逐帧视图会在 CamVid 原图上叠加 iPhone 真身检测到的物体框、"
        f"近/左/右三个判断区域及其状态，让你直接看清“为什么漏报/误阻挡”。</p>"
        f"<p><a href='{frames_url}'>→ 打开逐帧识别效果</a></p></div>"
    )
    case_script = f"""<script>
async function clusterCases() {{
  const status = document.getElementById('caseStatus');
  status.style.display = 'block'; status.className = 'status';
  status.textContent = '正在把失败帧聚类成 case…';
  try {{
    const url = '/diagnostics/cases/cluster?manifest={encoded_manifest}&predictions=' + encodeURIComponent('{encoded_pred}');
    const resp = await fetch(url, {{method: 'POST'}});
    const payload = await resp.json();
    if (!resp.ok) {{ status.className = 'status error'; status.textContent = '聚类失败：' + (payload.detail || resp.statusText); return; }}
    const parts = (payload.cases || []).map(c => c.title + '（' + c.frame_count + ' 帧，' + c.status_label + '）');
    status.className = 'status ok';
    status.innerHTML = '已生成/更新 ' + (payload.cases || []).length + ' 个 case：' + (parts.join('，') || '无失败帧')
      + " · <a href='/diagnostics/cases/ui'>查看 case 列表 →</a>";
  }} catch (error) {{ status.className = 'status error'; status.textContent = '请求失败：' + error; }}
}}
</script>"""
    case_callout = (
        "<div class='callout'><h2>闭环 case：把失败帧变成能跟踪、能重开的问题</h2>"
        "<p class='hint'>点一下，平台会把这次评估里的<b>漏报</b>和<b>误阻挡</b>帧按类型自动聚类成 case，"
        "每个 case 有确定性 id 和生命周期（新建→分诊→修复→验证）。"
        "同一问题下次再出现会<b>自动重开</b>——这就是「一个问题发生两次，就是系统没学会」的落地。</p>"
        "<button class='secondary' onclick='clusterCases()'>把失败帧聚成 case</button> "
        "<a href='/diagnostics/cases/ui' class='pill' style='text-decoration:none;border:1px solid #555;color:#d1d1d6'>查看 case 列表</a>"
        "<div id='caseStatus' class='status' style='display:none'></div></div>"
    )
    body = (
        header
        + parity_script
        + case_script
        + f"<div class='status ok'>已用 {html.escape(str(pred_path.name))} 对 iPhone 真身打分（prediction_source=ios_coreml_offline_harness）。</div>"
        + frames_callout
        + case_callout
        + cards
        + parity_card
        + f"<div class='card'><h2>建议</h2><ul>{recs}</ul></div>"
        + f"<details><summary>完整 JSON 报告</summary><pre>{details}</pre></details>"
        + steps
    )
    return _html_page("iPhone 真身评估", body)


def _repo_root() -> Path:
    # server-vqa/app/diagnostic_api.py -> repo root is two parents up from app/.
    return Path(__file__).resolve().parents[2]


def _harness_bin() -> Path:
    return _repo_root() / "ios-vqa-app" / "perception-harness" / ".build" / "debug" / "PerceptionHarness"


def _guidance_pairs(manifest_rows: list[dict], prediction_rows: list[dict]):
    """Build (frame_id, gt_path, pred_path) triples for line-level scoring.

    Malformed entries are counted as skipped (surfaced in the UI), never silently
    dropped — a frame missing GT or a well-formed prediction just doesn't score."""
    preds: dict = {}
    for row in prediction_rows:
        fid = row.get("frame_id")
        if fid is not None and isinstance(row.get("guidance_path"), dict):
            preds[fid] = row["guidance_path"]
    pairs = []
    skipped = 0
    for row in manifest_rows:
        fid = row.get("frame_id")
        gt_raw = row.get("ground_truth_path")
        pred_raw = preds.get(fid)
        if fid is None or not isinstance(gt_raw, dict) or pred_raw is None:
            continue
        try:
            pairs.append((fid, GuidancePath.from_dict(gt_raw), GuidancePath.from_dict(pred_raw)))
        except GuidancePathError:
            skipped += 1
    return pairs, skipped


def _harness_out_path(manifest_path: Path) -> Path:
    return Path(f"/tmp/{manifest_path.stem}-ios-harness.jsonl")


def _harness_meta_path(manifest_path: Path) -> Path:
    return Path(f"/tmp/{manifest_path.stem}-ios-harness.meta.json")


def _sha256_file(path: Path) -> str | None:
    """Content fingerprint of a file (first 16 hex of sha256). None on error so
    callers degrade gracefully instead of raising."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()[:16]
    except OSError:
        return None


def _write_harness_meta(manifest_path: Path, *, count: int, config_version, config_hash) -> None:
    """Write a sidecar fingerprint next to the predictions so freshness can be
    judged by CONTENT (manifest bytes + config behavior hash + harness binary
    fingerprint) rather than only file mtimes."""
    bin_path = _harness_bin()
    meta = {
        "manifest_hash": _sha256_file(manifest_path),
        "config_version": config_version,
        "config_hash": config_hash,
        "harness_hash": _sha256_file(bin_path) if bin_path.is_file() else None,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": count,
        "predictions": str(_harness_out_path(manifest_path)),
    }
    try:
        _harness_meta_path(manifest_path).write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # non-fatal: freshness will fall back to mtime heuristic


def _harness_cache_info(manifest_path: Path) -> dict:
    """Inspect any cached harness predictions for this manifest and decide whether
    they are still fresh. Prefers CONTENT fingerprints (meta sidecar) and falls
    back to mtime heuristics for predictions produced outside the server (manual
    runs). Re-running the on-device perception is only necessary when the dataset,
    the perception code/model, or the active config actually changed — surfaced
    explicitly so the user never re-runs blindly or trusts a stale result."""
    out_path = _harness_out_path(manifest_path)
    info: dict = {"out_path": str(out_path), "exists": out_path.is_file()}
    if not info["exists"]:
        return info
    try:
        stat = out_path.stat()
    except OSError:
        info["exists"] = False
        return info
    info["generated_at"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    count = 0
    cfg_version = None
    try:
        with open(out_path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                count += 1
                if cfg_version is None:
                    try:
                        row = json.loads(line)
                        cfg_version = (row.get("prediction") or {}).get("config_version")
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    info["count"] = count
    info["config_version"] = cfg_version

    try:
        active_cfg = load_active_config()
        active_version = active_cfg.version
        active_hash = active_cfg.content_hash()
    except ConfigValidationError:
        active_version = None
        active_hash = None
    info["active_config_version"] = active_version

    # Prefer the content-fingerprint meta sidecar when present (server-produced).
    meta = None
    meta_path = _harness_meta_path(manifest_path)
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = None

    reasons: list = []
    if meta:
        info["fingerprint"] = "content"
        info["generated_at"] = meta.get("generated_at", info["generated_at"])
        current_manifest_hash = _sha256_file(manifest_path)
        if (
            meta.get("manifest_hash")
            and current_manifest_hash
            and meta["manifest_hash"] != current_manifest_hash
        ):
            reasons.append("数据集 manifest 内容已变化（哈希不一致）")
        if (
            meta.get("config_hash")
            and active_hash
            and meta["config_hash"] != active_hash
        ):
            v_from = meta.get("config_version")
            reasons.append(
                f"感知配置行为已变化（v{v_from}→v{active_version}，ROI/阈值哈希不一致），需用新配置重跑"
            )
        current_bin_hash = _sha256_file(_harness_bin()) if _harness_bin().is_file() else None
        if (
            meta.get("harness_hash")
            and current_bin_hash
            and meta["harness_hash"] != current_bin_hash
        ):
            reasons.append("感知代码/模型已重新编译（harness 二进制哈希不一致）")
    else:
        # Fallback: no content fingerprint (e.g. manual harness run) -> mtime.
        info["fingerprint"] = "mtime"
        if cfg_version is not None and active_version is not None and cfg_version != active_version:
            reasons.append(f"感知配置已从 v{cfg_version} 更新到 v{active_version}，需用新配置重跑")
        try:
            if manifest_path.stat().st_mtime > stat.st_mtime:
                reasons.append("数据集 manifest 在预测生成后有改动（按时间近似判断）")
        except OSError:
            pass
        try:
            bin_path = _harness_bin()
            if bin_path.is_file() and bin_path.stat().st_mtime > stat.st_mtime:
                reasons.append("感知代码/模型已重新编译（按时间近似判断）")
        except OSError:
            pass

    info["stale_reasons"] = reasons
    info["fresh"] = not reasons
    return info


@router.post("/datasets/ios-harness/run")
def dataset_ios_harness_run(manifest: str, limit: int = 0, force: bool = False) -> dict:
    """Run the REAL on-device perception harness on this Mac, server-side.

    Removes the manual "open a terminal, swift build, copy the path" step: since
    the diagnostic server and the harness live on the same Mac, the server can
    invoke the already-built binary directly.

    You do NOT need to re-run every time: if fresh cached predictions already
    exist (dataset/model/config unchanged) it returns them as status="cached"
    unless force=true. The run evaluates the CURRENTLY ACTIVE perception config
    (via --config), so tuning the config and re-running actually changes results.

    Honest capability reporting (never a silent failure):
      - not macOS            -> status=unsupported (Core ML/Vision are Apple-only)
      - binary missing       -> best-effort `swift build`; if still missing, needs_build
      - harness non-zero rc  -> error with stderr tail (e.g. YOLO model not found)
    """
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")

    # Reuse cached predictions when nothing changed — cached results are
    # platform-independent to read, so allow this even off macOS.
    cache = _harness_cache_info(manifest_path)
    if not force and cache.get("exists") and cache.get("fresh"):
        return {
            "status": "cached",
            "predictions": cache["out_path"],
            "predicted": cache.get("count", 0),
            "config_version": cache.get("config_version"),
            "note": (
                f"复用上次结果：{cache.get('count', 0)} 帧，生成于 "
                f"{cache.get('generated_at', '?')}（配置未变，无需重跑）。"
            ),
        }

    if sys.platform != "darwin":
        return {
            "status": "unsupported",
            "capability": "not_macos",
            "reason": (
                f"当前服务器平台是 {sys.platform}，不是 macOS。iPhone 真身感知依赖 "
                "Core ML / Vision，只能在 Mac 上跑。请在 Mac 上运行诊断台，或按手动步骤执行。"
            ),
        }

    repo_root = _repo_root()
    harness_dir = repo_root / "ios-vqa-app" / "perception-harness"
    harness_bin = harness_dir / ".build" / "debug" / "PerceptionHarness"

    build_note = ""
    if not harness_bin.is_file():
        # Best-effort build. The harness has no third-party deps (only Apple
        # frameworks + symlinked app sources), so this is offline-capable.
        try:
            build = subprocess.run(
                ["swift", "build"],
                cwd=str(harness_dir),
                capture_output=True,
                text=True,
                timeout=600,
            )
        except FileNotFoundError:
            return {
                "status": "unsupported",
                "capability": "needs_build",
                "reason": "找不到 swift 工具链。请安装 Xcode Command Line Tools 后重试，或手动执行 swift build。",
            }
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "capability": "needs_build",
                "reason": "swift build 超时（>600s）。请在 Mac 终端手动执行 swift build 后重试。",
            }
        if build.returncode != 0 or not harness_bin.is_file():
            tail = (build.stderr or build.stdout or "").strip().splitlines()[-12:]
            return {
                "status": "error",
                "capability": "needs_build",
                "reason": "自动编译失败，请在 Mac 终端手动执行 `cd ios-vqa-app/perception-harness && swift build` 查看完整报错。",
                "build_stderr": "\n".join(tail),
            }
        build_note = "已自动编译 harness。"

    out_path = str(_harness_out_path(manifest_path))
    cmd = [str(harness_bin), "--manifest", str(manifest_path), "--out", out_path]
    if limit and limit > 0:
        cmd += ["--limit", str(limit)]

    # Evaluate the CURRENTLY ACTIVE perception config so tuning it (and bumping
    # the version) is reflected in the harness result — this is what makes the
    # tune -> re-run -> gate -> ship loop coherent. Fall back to compiled defaults
    # (no --config) if the active config can't be serialized; never fake success.
    config_file = None
    try:
        active_cfg = load_active_config().to_dict()
        config_file = Path(f"/tmp/{manifest_path.stem}-perception-config.json")
        config_file.write_text(json.dumps(active_cfg), encoding="utf-8")
        cmd += ["--config", str(config_file)]
    except (ConfigValidationError, OSError):
        config_file = None
    try:
        run = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "reason": "真身感知运行超时（>900s）。可用 limit 参数先跑少量帧验证，或在终端手动执行。",
        }

    stderr_tail = (run.stderr or "").strip().splitlines()[-8:]
    if run.returncode != 0:
        return {
            "status": "error",
            "reason": "真身感知运行失败（非零退出）。常见原因：缺少 YOLO Core ML 模型。见下方 stderr。",
            "returncode": run.returncode,
            "stderr": "\n".join(stderr_tail),
        }

    try:
        predicted = sum(1 for _ in open(out_path, "r", encoding="utf-8"))
    except OSError:
        predicted = 0
    if predicted == 0:
        return {
            "status": "error",
            "reason": f"运行结束但未产出预测（{out_path} 为空）。见下方 stderr。",
            "stderr": "\n".join(stderr_tail),
        }

    cfg_version = active_cfg.get("version") if config_file else None
    cfg_hash = active_cfg.get("hash") if config_file else None
    _write_harness_meta(
        manifest_path, count=predicted, config_version=cfg_version, config_hash=cfg_hash
    )
    return {
        "status": "ok",
        "predictions": out_path,
        "predicted": predicted,
        "config_version": cfg_version,
        "note": (
            build_note
            + f"已在本机跑完真身感知，产出 {predicted} 帧预测"
            + (f"（配置 v{cfg_version}）。" if cfg_version is not None else "。")
        ).strip(),
        "stderr": "\n".join(stderr_tail),
    }


# Region/status colors shared by the per-frame overlay. Mirrors the app's
# green=go / yellow=caution / red=blocked / gray=unknown language.
_STATUS_COLOR = {
    "candidateOpen": "#30d158",
    "caution": "#ffd60a",
    "blocked": "#ff453a",
    "unknown": "#8e8e93",
}
_STATUS_LABEL = {
    "candidateOpen": "可走候选",
    "caution": "注意",
    "blocked": "疑似占用",
    "unknown": "信息不足",
}
IOS_FRAMES_PAGE_SIZE = 12


def _guidance_line_svg(
    path: dict, *, color: str, dashed: bool, corridor: bool, label: str = "", width: float = 1.4
) -> str:
    """Render one guidance line as an SVG polyline (+ optional corridor band).

    Points are Vision-normalized (origin lower-left, y up); flip y for screen.
    Returns "" when the path is missing/insufficient so a degrade shows as an
    absent line rather than a fabricated straight one. ``label`` (预测/真值) is
    drawn at the forward end so the line is self-explanatory even without legend."""
    if not isinstance(path, dict) or path.get("status") != "ok":
        return ""
    lines = path.get("lines") or []
    if not lines:
        return ""
    primary = lines[0]
    points = primary.get("points") or []
    if len(points) < 2:
        return ""

    def sx(p):
        return float(p.get("x", 0.0)) * 100.0

    def sy(p):
        return (1.0 - float(p.get("y", 0.0))) * 100.0

    parts: list[str] = []
    if corridor:
        left = [f"{max(0.0, sx(p) - float(p.get('half_width', 0.0)) * 100.0):.2f},{sy(p):.2f}" for p in points]
        right = [f"{min(100.0, sx(p) + float(p.get('half_width', 0.0)) * 100.0):.2f},{sy(p):.2f}" for p in reversed(points)]
        poly = " ".join(left + right)
        parts.append(
            f"<polygon points='{poly}' fill='{color}' fill-opacity='0.12' stroke='none'/>"
        )
    pts = " ".join(f"{sx(p):.2f},{sy(p):.2f}" for p in points)
    dash = " stroke-dasharray='2.2 1.6'" if dashed else ""
    # A faint dark halo under the line keeps it legible over bright/pale scenery.
    parts.append(
        f"<polyline points='{pts}' fill='none' stroke='#000' stroke-opacity='0.35' "
        f"stroke-width='{width + 1.0:.2f}' stroke-linejoin='round' stroke-linecap='round'/>"
    )
    parts.append(
        f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='{width:.2f}' "
        f"stroke-linejoin='round' stroke-linecap='round'{dash}/>"
    )
    # Mark the start (feet) with a small dot.
    parts.append(f"<circle cx='{sx(points[0]):.2f}' cy='{sy(points[0]):.2f}' r='1.2' fill='{color}'/>")
    if label:
        fx = min(88.0, sx(points[-1]) + 1.2)
        fy = max(4.0, sy(points[-1]))
        parts.append(
            f"<text x='{fx:.2f}' y='{fy:.2f}' fill='{color}' stroke='#000' stroke-width='0.25' "
            f"paint-order='stroke' font-size='3.4' font-weight='800'>{html.escape(label)}</text>"
        )
    return "".join(parts)


def _overlay_svg(
    roi: dict,
    objects: list,
    prediction: dict,
    guidance_path: dict | None = None,
    gt_path: dict | None = None,
) -> str:
    """Build an SVG overlay (viewBox 0..100, stretched to the image) drawing the
    three decision ROIs colored by predicted status plus the detected object
    boxes, and (when present) the predicted vs ground-truth guidance lines.
    Vision-normalized coords have origin lower-left, so y is flipped for the
    top-left screen space of an <img>."""

    def to_screen(box: dict) -> tuple:
        x = float(box.get("x", 0.0)) * 100.0
        w = float(box.get("w", 0.0)) * 100.0
        h = float(box.get("h", 0.0)) * 100.0
        y = (1.0 - (float(box.get("y", 0.0)) + float(box.get("h", 0.0)))) * 100.0
        return x, y, w, h

    parts = [
        "<svg viewBox='0 0 100 100' preserveAspectRatio='none' "
        "xmlns='http://www.w3.org/2000/svg'>"
    ]

    roi_regions = [
        ("near", prediction.get("near_path_status", "unknown"), "近"),
        ("left", prediction.get("left_front_status", "unknown"), "左"),
        ("right", prediction.get("right_front_status", "unknown"), "右"),
    ]
    for key, status, short in roi_regions:
        rect = (roi or {}).get(key)
        if not isinstance(rect, dict):
            continue
        x, y, w, h = to_screen(rect)
        color = _STATUS_COLOR.get(str(status), "#8e8e93")
        # ROI status is now the SECONDARY (legacy coarse) signal — draw it faint so
        # it reads as background context and does not fight the guidance line.
        parts.append(
            f"<rect x='{x:.2f}' y='{y:.2f}' width='{w:.2f}' height='{h:.2f}' "
            f"fill='{color}' fill-opacity='0.06' stroke='{color}' stroke-opacity='0.55' "
            f"stroke-width='0.5' stroke-dasharray='1.2 1.0'/>"
        )
        label = f"{short} {_STATUS_LABEL.get(str(status), status)}"
        ty = max(3.0, y + 3.0)
        parts.append(
            f"<text x='{x + 0.8:.2f}' y='{ty:.2f}' fill='{color}' fill-opacity='0.8' "
            f"font-size='2.8' font-weight='600'>{html.escape(label)}</text>"
        )

    for obj in objects or []:
        box = obj.get("box") if isinstance(obj, dict) else None
        if not isinstance(box, dict):
            continue
        x, y, w, h = to_screen(box)
        conf = obj.get("confidence")
        try:
            conf_txt = f" {round(float(conf) * 100)}%"
        except (TypeError, ValueError):
            conf_txt = ""
        label = f"{obj.get('label') or obj.get('kind') or '物体'}{conf_txt}"
        parts.append(
            f"<rect x='{x:.2f}' y='{y:.2f}' width='{w:.2f}' height='{h:.2f}' "
            f"fill='none' stroke='#0a84ff' stroke-width='0.7' stroke-dasharray='1.4 0.8'/>"
        )
        ty = max(2.8, y - 0.8)
        parts.append(
            f"<text x='{x + 0.5:.2f}' y='{ty:.2f}' fill='#64d2ff' "
            f"font-size='3.0' font-weight='700'>{html.escape(label)}</text>"
        )

    # The guidance LINE is the primary signal. Ground-truth line (green dashed,
    # thinner) vs predicted line (purple solid, thicker, with a faint corridor
    # band). Draw GT first so the prediction sits on top, and label both ends.
    if gt_path:
        parts.append(_guidance_line_svg(gt_path, color="#30d158", dashed=True, corridor=False, label="真值", width=1.4))
    if guidance_path:
        parts.append(_guidance_line_svg(guidance_path, color="#bf5af2", dashed=False, corridor=True, label="预测", width=2.4))

    parts.append("</svg>")
    return "".join(parts)


_FRAME_REGION_KEYS = ("near_path_status", "left_front_status", "right_front_status")

# Filter definitions for the per-frame viewer: id -> (label, hint). Order here is
# the order shown in the selector. "all" is implicit and always first.
_FRAME_FILTERS = {
    "risk_miss": ("漏报", "真实 注意/占用，却报 可走候选（最危险）"),
    "false_block": ("误阻挡", "真实 可走，却报 注意/占用（过度保守）"),
    "mismatch": ("有分歧", "任一区域预测≠真实"),
    "correct": ("全对", "三区域预测与真实全一致"),
    "no_prediction": ("无预测", "该帧没有对应预测行"),
}


def _frame_flags(gt: dict, prediction: dict) -> set:
    """Classify one frame into filter buckets from GT vs prediction. Empty
    prediction -> {"no_prediction"}; otherwise a frame may carry several tags
    (e.g. both risk_miss and false_block across different regions).

    The safety-relevant buckets (risk_miss / false_block) come from
    ``case_store.frame_failure_types`` so the UI filters and the case clusters
    are guaranteed to agree on what counts as a failure."""
    if not prediction:
        return {"no_prediction"}
    flags = set(frame_failure_types(gt, prediction))
    all_present_equal = True
    for key in _FRAME_REGION_KEYS:
        g = gt.get(key)
        p = prediction.get(key)
        if g is None or p is None:
            all_present_equal = False
            continue
        if g != p:
            all_present_equal = False
            flags.add("mismatch")
    if all_present_equal:
        flags.add("correct")
    return flags


@router.get("/datasets/ios-harness/frames/ui", response_class=HTMLResponse)
def dataset_ios_harness_frames_ui(
    manifest: str, predictions: str, page: int = 1, filter: str = "all"
):
    """Per-frame visualization: draw the iPhone on-device perception output
    (detected object boxes + near/left/right ROI status) on top of each CamVid
    image, side by side with the ground-truth answer. This is the "看得见" view
    that turns aggregate metrics into inspectable pictures."""
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")
    pred_path = Path(predictions).expanduser()
    if not pred_path.is_file():
        raise HTTPException(status_code=404, detail="predictions_not_found")

    manifest_rows = load_jsonl(manifest_path)
    pred_index: dict = {}
    for row in load_jsonl(pred_path):
        fid = row.get("frame_id")
        if fid is not None:
            pred_index[str(fid)] = row

    # Classify every frame once so we can both filter and show per-category counts
    # (with 701 frames the user needs to jump straight to the bad ones).
    flags_by_index: list = []
    counts = {key: 0 for key in _FRAME_FILTERS}
    for row in manifest_rows:
        gt = row.get("ground_truth", {}) or {}
        pred = (pred_index.get(str(row.get("frame_id", ""))) or {}).get("prediction", {}) or {}
        flags = _frame_flags(gt, pred)
        flags_by_index.append(flags)
        for key in flags:
            if key in counts:
                counts[key] += 1

    if filter not in _FRAME_FILTERS:
        filter = "all"
    if filter == "all":
        filtered_rows = manifest_rows
    else:
        filtered_rows = [
            row for row, flags in zip(manifest_rows, flags_by_index) if filter in flags
        ]

    grand_total = len(manifest_rows)
    total = len(filtered_rows)
    total_pages = max(1, (total + IOS_FRAMES_PAGE_SIZE - 1) // IOS_FRAMES_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * IOS_FRAMES_PAGE_SIZE
    page_rows = filtered_rows[start : start + IOS_FRAMES_PAGE_SIZE]

    encoded_manifest = html.escape(str(manifest_path))
    encoded_pred = html.escape(str(pred_path))

    def status_pill(status: str) -> str:
        color = _STATUS_COLOR.get(str(status), "#8e8e93")
        label = _STATUS_LABEL.get(str(status), str(status))
        return (
            f"<span class='pill' style='border:1px solid {color};color:{color}'>"
            f"{html.escape(label)}</span>"
        )

    cards = []
    for row in page_rows:
        frame_id = str(row.get("frame_id", ""))
        image_path = str(row.get("image_path") or "")
        gt = row.get("ground_truth", {}) or {}
        pred_row = pred_index.get(frame_id, {})
        prediction = pred_row.get("prediction", {}) or {}
        objects = pred_row.get("objects", []) or []
        roi = pred_row.get("roi", {}) or {}
        pred_guidance = pred_row.get("guidance_path") if isinstance(pred_row.get("guidance_path"), dict) else None
        gt_guidance = row.get("ground_truth_path") if isinstance(row.get("ground_truth_path"), dict) else None

        if not image_path:
            image_block = "<p class='muted'>无图片路径</p>"
        elif not prediction:
            thumb = f"/diagnostics/local-file?path={html.escape(image_path)}&w=560"
            image_block = (
                f"<div class='frame-overlay'><img loading='lazy' decoding='async' "
                f"src='{thumb}' alt='{html.escape(frame_id)}'></div>"
                f"<p class='muted'>该帧没有对应预测（可能被 --limit 截断）。</p>"
            )
        else:
            thumb = f"/diagnostics/local-file?path={html.escape(image_path)}&w=560"
            full = f"/diagnostics/local-file?path={html.escape(image_path)}"
            overlay = _overlay_svg(roi, objects, prediction, pred_guidance, gt_guidance)
            image_block = (
                f"<a href='{full}' target='_blank'><div class='frame-overlay'>"
                f"<img loading='lazy' decoding='async' src='{thumb}' alt='{html.escape(frame_id)}'>"
                f"{overlay}</div></a>"
            )

        def region_row(name: str, key: str) -> str:
            g = gt.get(key, "—")
            p = prediction.get(key, "—")
            flag = ""
            if g in ("caution", "blocked") and p == "candidateOpen":
                flag = " <span style='color:#ff453a'>⚠ 漏报</span>"
            elif g == "candidateOpen" and p in ("caution", "blocked"):
                flag = " <span style='color:#ffd60a'>误阻挡</span>"
            return (
                f"<tr><td>{name}</td><td>{status_pill(g)}</td>"
                f"<td>{status_pill(p) if prediction else '—'}{flag}</td></tr>"
            )

        obj_labels = ", ".join(
            html.escape(str(o.get("label") or o.get("kind") or "物体")) for o in objects
        ) or "（未检出物体）"

        cards.append(
            f"""<div class='card'><h2>{html.escape(frame_id)}</h2>
<div class='row'>
  <div>{image_block}
    <p class='explain'><b style='color:#bf5af2'>紫实线=预测路径</b> · <b style='color:#30d158'>绿虚线=真值路径</b>（主信号，越贴合越准）；蓝虚框=检测物体；淡色绿/黄/红方块=近/左/右三区状态（背景参考）。</p>
  </div>
  <div>
    <table>
      <tr><th>区域</th><th>真实答案</th><th>iPhone 预测</th></tr>
      {region_row('近处', 'near_path_status')}
      {region_row('左前', 'left_front_status')}
      {region_row('右前', 'right_front_status')}
    </table>
    <p class='explain'>关注方向：真实 {html.escape(str(gt.get('focus_direction', '—')))} / 预测 {html.escape(str(prediction.get('focus_direction', '—')))}</p>
    <p class='explain'>检出物体：{obj_labels}</p>
  </div>
</div></div>"""
        )

    def page_url(p: int) -> str:
        return (
            f"/diagnostics/datasets/ios-harness/frames/ui?manifest={encoded_manifest}"
            f"&predictions={encoded_pred}&filter={filter}&page={p}"
        )

    def filter_url(f: str) -> str:
        return (
            f"/diagnostics/datasets/ios-harness/frames/ui?manifest={encoded_manifest}"
            f"&predictions={encoded_pred}&filter={f}"
        )

    # Filter selector: one pill per bucket, active one highlighted, each with a
    # live count so the user knows how many bad frames exist before clicking.
    def filter_pill(f: str, label: str, count: int, hint: str = "") -> str:
        active = f == filter
        style = (
            "background:#0a84ff;color:#fff;border:1px solid #0a84ff"
            if active
            else "background:#2c2c2e;color:#d1d1d6;border:1px solid #555"
        )
        title = f" title='{html.escape(hint)}'" if hint else ""
        return (
            f"<a href='{filter_url(f)}' class='pill' style='{style};text-decoration:none'{title}>"
            f"{html.escape(label)} <b>{count}</b></a>"
        )

    filter_pills = [filter_pill("all", "全部", grand_total)]
    for key, (label, hint) in _FRAME_FILTERS.items():
        filter_pills.append(filter_pill(key, label, counts[key], hint))
    filter_bar = (
        "<div class='card'><h3 style='margin:0 0 8px'>只看哪种结果</h3>"
        "<p class='hint' style='margin:0 0 10px'>帧较多时用它直接跳到关心的样本，"
        "尤其是“漏报”和“误阻挡”这两类最该复盘。</p>"
        + " ".join(filter_pills)
        + "</div>"
    )

    nav_bits = [f"<span class='muted'>本类 {total} 帧 · 第 {page}/{total_pages} 页</span>"]
    if page > 1:
        nav_bits.append(f"<a href='{page_url(page - 1)}'>← 上一页</a>")
    if page < total_pages:
        nav_bits.append(f"<a href='{page_url(page + 1)}'>下一页 →</a>")
    nav = "<p class='hint'>" + " · ".join(nav_bits) + "</p>"

    empty_msg = (
        "<p class='muted'>该类别下没有帧——挺好，说明这种问题不存在。换个筛选看看。</p>"
        if filter != "all"
        else "<p>没有可显示的帧。</p>"
    )

    header = (
        f"<p><a href='/diagnostics/datasets/ios-harness/ui?manifest={encoded_manifest}"
        f"&predictions={encoded_pred}'>← 返回 iPhone 真身评估</a> · "
        f"<a href='/diagnostics/datasets/manifest/ui?manifest={encoded_manifest}'>浏览 manifest</a></p>"
        f"<h1>逐帧识别效果：{html.escape(manifest_path.name)}</h1>"
        f"<div class='callout'><p class='hint'>每张图上叠加的是 iPhone 上一模一样的感知代码（YOLO11n Core ML + 通行区域引擎）真实跑出的结果。</p>"
        f"<p class='hint' style='margin-top:6px'><b>主信号 · 引导线</b>（我们要评的就是它）："
        f"<span style='color:#bf5af2;font-weight:800'>▬ 紫实线=iPhone 预测路径</span>（带浅色走廊=可走宽度）， "
        f"<span style='color:#30d158;font-weight:800'>┄ 绿虚线=真值路径</span>（你 CamVid 标注推出的答案）。两条越贴合越准。</p>"
        f"<p class='hint' style='margin-top:6px'><b>辅助 · 背景</b>："
        f"<span style='color:#64d2ff'>蓝虚框</span>=YOLO 检测物体； "
        f"淡色<span style='color:#30d158'>绿</span>/<span style='color:#ffd60a'>黄</span>/"
        f"<span style='color:#ff453a'>红</span>方块=近/左/右三区状态（旧的粗粒度分区，已弱化为背景，右表仍按它对比）。</p></div>"
    )

    body = header + filter_bar + nav + ("".join(cards) or empty_msg) + (nav if cards else "")
    return _html_page("逐帧识别效果", body)


@router.post("/cases/cluster")
def cases_cluster(manifest: str, predictions: str) -> dict:
    """Cluster this eval run's failing frames into cases (create or update).

    This is the AutoTriage-lite entry: read the manifest + harness predictions,
    bucket risk_miss / false_block frames, and upsert a case per bucket with a
    deterministic id so re-runs update instead of duplicate."""
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")
    pred_path = Path(predictions).expanduser()
    if not pred_path.is_file():
        raise HTTPException(status_code=404, detail="predictions_not_found")

    manifest_rows = load_jsonl(manifest_path)
    pred_index: dict = {}
    for row in load_jsonl(pred_path):
        fid = row.get("frame_id")
        if fid is not None:
            pred_index[str(fid)] = row

    dataset_key = dataset_key_from_manifest(manifest_path)
    clusters = cluster_failures(manifest_rows, pred_index, dataset_key=dataset_key)
    source = f"cluster:{pred_path.name}"
    cases = upsert_clusters(clusters, source=source)
    return {
        "status": "ok",
        "dataset_key": dataset_key,
        "cases": [
            {
                "case_id": c["case_id"],
                "title": c["title"],
                "failure_type": c["failure_type"],
                "frame_count": c["frame_count"],
                "status": c["status"],
                "status_label": case_status_label(c["status"]),
            }
            for c in cases
        ],
    }


@router.get("/cases")
def cases_list() -> dict:
    return {"cases": list_cases()}


@router.post("/cases/status")
def cases_set_status(id: str, status: str, note: str = "") -> dict:
    try:
        case = case_set_status(id, status, note=note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "case": case}


@router.post("/cases/annotate")
def cases_annotate(id: str, suspected_cause: Optional[str] = Body(default=None), linked_fix: Optional[str] = Body(default=None)) -> dict:
    try:
        case = case_annotate(id, suspected_cause=suspected_cause, linked_fix=linked_fix)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "case": case}


_CASE_STATUS_COLOR = {
    "new": "#0a84ff",
    "triaged": "#5e5ce6",
    "fixing": "#ff9f0a",
    "verified": "#30d158",
    "released": "#64d2ff",
    "reopened": "#ff453a",
    "closed": "#8e8e93",
}


def _case_status_pill(status: str) -> str:
    color = _CASE_STATUS_COLOR.get(status, "#8e8e93")
    return (
        f"<span class='pill' style='border:1px solid {color};color:{color}'>"
        f"{html.escape(case_status_label(status))}</span>"
    )


@router.get("/cases/ui", response_class=HTMLResponse)
def cases_ui():
    """Case list: the closed-loop backlog. Open/reopened cases float to the top."""
    cases = list_cases()
    if not cases:
        body = (
            "<h1>闭环 case 列表</h1>"
            "<div class='callout'><p>还没有 case。</p>"
            "<p class='hint'>去「iPhone 真身评估」页跑一次评估，点<b>把失败帧聚成 case</b>，"
            "平台会把漏报/误阻挡帧自动聚类成可跟踪的 case。</p></div>"
        )
        return _html_page("闭环 case 列表", body)

    rows = []
    for c in cases:
        cid = html.escape(str(c.get("case_id", "")))
        detail = f"/diagnostics/cases/detail/ui?id={cid}"
        fix = html.escape(str(c.get("linked_fix") or ""))
        fix_cell = f"<span class='muted'>{fix}</span>" if fix else "<span class='muted'>—</span>"
        rows.append(
            f"<tr>"
            f"<td><a href='{detail}'>{html.escape(str(c.get('title', c.get('case_id'))))}</a></td>"
            f"<td>{_case_status_pill(str(c.get('status', 'new')))}</td>"
            f"<td style='text-align:right'>{int(c.get('frame_count') or 0)}</td>"
            f"<td>{html.escape(case_failure_label(str(c.get('failure_type', ''))))}</td>"
            f"<td>{fix_cell}</td>"
            f"<td class='muted'>{html.escape(str(c.get('updated_at', ''))[:19])}</td>"
            f"</tr>"
        )

    open_count = sum(1 for c in cases if c.get("status") not in {"verified", "released", "closed"})
    body = (
        "<h1>闭环 case 列表</h1>"
        f"<p class='hint'>共 {len(cases)} 个 case，其中 <b>{open_count}</b> 个未收敛。"
        "借鉴 DCL 的「统一载体 + 生命周期」，把「失败帧」变成能跟踪到关闭的问题。</p>"
        "<table><tr><th>Case</th><th>状态</th><th style='text-align:right'>帧数</th>"
        "<th>类型</th><th>关联修复</th><th>更新时间</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    return _html_page("闭环 case 列表", body)


@router.get("/cases/detail/ui", response_class=HTMLResponse)
def cases_detail_ui(id: str):
    case = load_case(id)
    if case is None:
        raise HTTPException(status_code=404, detail="case_not_found")

    cid = html.escape(str(case.get("case_id", "")))
    status = str(case.get("status", "new"))

    # Lifecycle buttons: offer the sensible next states plus close/reopen.
    status_buttons = "".join(
        f"<button class='secondary' onclick=\"setStatus('{s}')\">{html.escape(case_status_label(s))}</button> "
        for s in CASE_STATUSES
        if s != status
    )

    frame_ids = case.get("frame_ids", []) or []
    shown = frame_ids[:60]
    frame_list = ", ".join(html.escape(str(f)) for f in shown) or "（无）"
    more = f"…… 等共 {len(frame_ids)} 帧" if len(frame_ids) > len(shown) else ""

    hist_rows = []
    for h in reversed(case.get("history", []) or []):
        at = html.escape(str(h.get("at", ""))[:19])
        event = html.escape(str(h.get("event", "")))
        detail_bits = []
        if "from" in h or "to" in h:
            detail_bits.append(f"{html.escape(str(h.get('from', '')))}→{html.escape(str(h.get('to', '')))}")
        if h.get("frame_count") is not None:
            detail_bits.append(f"{h.get('frame_count')} 帧")
        if h.get("note"):
            detail_bits.append(html.escape(str(h.get("note"))))
        if h.get("fields"):
            detail_bits.append("改：" + html.escape(", ".join(h.get("fields", []))))
        hist_rows.append(f"<tr><td class='muted'>{at}</td><td>{event}</td><td>{' · '.join(detail_bits)}</td></tr>")

    first_seen = case.get("first_seen", {}) or {}
    suspected = html.escape(str(case.get("suspected_cause") or ""))
    linked_fix = html.escape(str(case.get("linked_fix") or ""))

    script = f"""<script>
async function setStatus(s) {{
  const st = document.getElementById('opStatus');
  st.style.display='block'; st.className='status'; st.textContent='更新状态中…';
  const note = document.getElementById('statusNote').value || '';
  try {{
    const url = '/diagnostics/cases/status?id={cid}&status=' + encodeURIComponent(s) + '&note=' + encodeURIComponent(note);
    const resp = await fetch(url, {{method:'POST'}});
    const p = await resp.json();
    if(!resp.ok){{ st.className='status error'; st.textContent='失败：'+(p.detail||resp.statusText); return; }}
    location.reload();
  }} catch(e) {{ st.className='status error'; st.textContent='请求失败：'+e; }}
}}
async function saveNotes() {{
  const st = document.getElementById('opStatus');
  st.style.display='block'; st.className='status'; st.textContent='保存中…';
  const body = {{
    suspected_cause: document.getElementById('suspected').value,
    linked_fix: document.getElementById('linkedfix').value
  }};
  try {{
    const resp = await fetch('/diagnostics/cases/annotate?id={cid}', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
    const p = await resp.json();
    if(!resp.ok){{ st.className='status error'; st.textContent='失败：'+(p.detail||resp.statusText); return; }}
    st.className='status ok'; st.textContent='已保存。';
  }} catch(e) {{ st.className='status error'; st.textContent='请求失败：'+e; }}
}}
</script>"""

    body = (
        script
        + "<p><a href='/diagnostics/cases/ui'>← 返回 case 列表</a></p>"
        + f"<h1>{html.escape(str(case.get('title', case.get('case_id'))))}</h1>"
        + f"<p>{_case_status_pill(status)} <span class='muted'>id: {cid}</span> · "
        + f"类型 {html.escape(case_failure_label(str(case.get('failure_type', ''))))} · "
        + f"当前 {int(case.get('frame_count') or 0)} 帧</p>"
        + f"<p class='hint'>首次出现：{html.escape(str(first_seen.get('at', ''))[:19])}"
        + f"（{first_seen.get('frame_count', '?')} 帧，来源 {html.escape(str(first_seen.get('source', '')))}）</p>"
        + "<div class='card'><h2>推进生命周期</h2>"
        + "<input type='text' id='statusNote' placeholder='本次状态变更备注（可选）' style='width:100%;margin-bottom:8px'>"
        + status_buttons
        + "<div id='opStatus' class='status' style='display:none'></div></div>"
        + "<div class='card'><h2>分诊笔记 / 关联修复</h2>"
        + f"<label class='num'>疑似根因<textarea id='suspected' rows='2' style='width:100%'>{suspected}</textarea></label>"
        + f"<label class='num'>关联修复（commit / 文档路径）<input type='text' id='linkedfix' value='{linked_fix}' style='width:100%'></label>"
        + "<button class='secondary' onclick='saveNotes()'>保存笔记</button></div>"
        + f"<div class='card'><h2>失败帧（{len(frame_ids)}）</h2><p class='muted'>{frame_list} {more}</p></div>"
        + "<div class='card'><h2>历史</h2><table><tr><th>时间</th><th>事件</th><th>说明</th></tr>"
        + ("".join(hist_rows) or "<tr><td colspan='3' class='muted'>无</td></tr>")
        + "</table></div>"
    )
    return _html_page(f"Case {case.get('case_id')}", body)


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
