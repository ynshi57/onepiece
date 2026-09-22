"""HTTP API for local diagnostic capture management."""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from PIL import Image
from pydantic import BaseModel

from app.dataset_paths import (
    open_dataset_root,
    remap_stale_dataset_path,
    resolve_dataset_file,
    rewrite_manifest_resolved_paths,
)
from app.case_store import (
    annotate as case_annotate,
    cluster_failures,
    dataset_key_from_manifest,
    failure_label as case_failure_label,
    frame_failure_types,
    _grid_cells,
    guidance_payloads,
    list_cases,
    load_case,
    set_status as case_set_status,
    status_label as case_status_label,
    upsert_clusters,
    CASE_STATUSES,
)
from app.diagnostic_capture import capture_root, get_session_dir, list_sessions
from app.diagnostic_report import generate_report_from_session_dir
from app.eval_baseline import baseline_root, list_baselines, load_baseline, save_baseline
from app.open_dataset_adapters import (
    create_bdd100k_drivable_manifest,
    create_camvid_manifest,
    camvid_traversable_colors,
    _camvid_traversability_mask,
)
from app.region_grid import evaluate_region_grids
from app.path_dataset_eval import evaluate_path_guidance, load_jsonl
from app.path_dataset_import import create_manifest_from_folders
from app.path_manifest_export import export_session_path_manifest, manifest_to_jsonl
from app.path_parity import compute_parity
from app.perception_config import (
    ConfigValidationError,
    bump_and_save,
    config_from_dict,
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
    .gallery {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; }}
    .gallery img {{ max-width: 100%; width: 100%; height: auto; }}
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
    .frame-overlay .gt-mask {{ position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }}
    .frame-overlay .pred-mask {{ position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; image-rendering: pixelated; }}
    .frame-overlay svg {{ position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }}
    .hide-gt-mask .gt-mask {{ display: none; }}
    .hide-pred-mask .pred-mask {{ display: none; }}
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


# --- Capability overview (single north-star scorecard) -----------------------
# The platform grew one evaluation page at a time; users could see many scattered
# metrics but never a single answer to "how good is iPhone local perception right
# now, and is it getting better or worse?". These helpers read the committed,
# gate-protected baselines (the authoritative current level) and turn them into a
# plain-language verdict + trend so the landing page can lead with that answer.
CAPABILITY_REGION_BASELINE = "camvid-ios-region"
CAPABILITY_ROLE_BASELINE = "camvid-ios-role"
CAPABILITY_DRIVE_BASELINE = "camvid-ios-drive"
CAPABILITY_LANE_BASELINE = "camvid-ios-lane"
CAPABILITY_OBSTACLE_BASELINE = "camvid-ios-obstacle"
CAPABILITY_REPORT_FILE = "camvid-ios-report.json"
# Above this share of "walkable" cells actually being road, a walker is being
# routed into traffic — treated as a safety red-line for the walk role.
ROLE_ROAD_AS_PRIMARY_REDLINE = 0.20


def _capability_report_metrics() -> Optional[dict]:
    """Latest full eval report (live region + guidance metrics), if present.

    Used only for trend vs the committed baseline. Absent report is a normal
    state (never crash the overview), not a silent failure of the baselines.
    """
    path = baseline_root() / CAPABILITY_REPORT_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _capability_snapshot() -> dict:
    """Compute the iPhone-perception capability verdict from committed baselines.

    Pure/JSON-only so it is unit-testable and safe to commit. Returns
    ``{"available": False, "reason": ...}`` when a baseline is missing rather than
    fabricating a fake score (no silent pass).
    """
    region_b = load_baseline(CAPABILITY_REGION_BASELINE)
    if not region_b:
        return {
            "available": False,
            "reason": "尚无「可走区域」能力基线。请先在数据集上跑一次「iPhone 真身评估」并保存基线，这里才能给出定级。",
        }

    region = region_b.get("metrics", {}) if isinstance(region_b.get("metrics"), dict) else {}

    def _f(d: dict, k: str) -> float:
        try:
            return float(d.get(k) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _i(d: dict, k: str) -> int:
        try:
            return int(d.get(k) or 0)
        except (TypeError, ValueError):
            return 0

    iou = _f(region, "mean_iou")
    precision = _f(region, "mean_precision")
    recall = _f(region, "mean_recall")
    region_false_go = _i(region, "region_false_go_frames")
    region_miss = _i(region, "region_miss_frames")
    scored = _i(region, "scored")
    # Role-conditioned (walk) baseline is optional: it measures against a DIFFERENT
    # truth definition (sidewalk=walkable, road=caution), so we surface it as its own
    # honest section rather than folding it into the binary-GT `safe` verdict.
    role_b = load_baseline(CAPABILITY_ROLE_BASELINE)
    role_section = None
    if role_b:
        rm = role_b.get("metrics", {}) if isinstance(role_b.get("metrics"), dict) else {}
        role_section = {
            "road_as_primary_rate": _f(rm, "mean_road_as_primary_rate"),
            "primary_recall": _f(rm, "mean_primary_recall"),
            "lane_coverage": _f(rm, "mean_lane_coverage"),
            "obstacle_overlap_rate": _f(rm, "mean_obstacle_overlap_rate"),
            "road_as_primary_frames": _i(rm, "road_as_primary_frames"),
            "obstacle_overlap_frames": _i(rm, "obstacle_overlap_frames"),
            "scored": _i(rm, "scored"),
        }

    # Driving-role baseline (SAME binary device grid, scored against the DRIVER's
    # truth: road=primary, sidewalk=caution/obstacle). The contrast walk-vs-drive is
    # the whole point of "区分人车": one prediction can be safe for a driver yet
    # dangerous for a walker. Optional; absent => no drive section.
    drive_b = load_baseline(CAPABILITY_DRIVE_BASELINE)
    drive_section = None
    if drive_b:
        dm = drive_b.get("metrics", {}) if isinstance(drive_b.get("metrics"), dict) else {}
        drive_section = {
            "road_recall": _f(dm, "mean_primary_recall"),
            "sidewalk_as_road_rate": _f(dm, "mean_road_as_primary_rate"),
            "sidewalk_as_road_frames": _i(dm, "road_as_primary_frames"),
            "obstacle_overlap_frames": _i(dm, "obstacle_overlap_frames"),
            "scored": _i(dm, "scored"),
        }

    # Lane-marking capability (dedicated binary lane segmenter, held-out CamVid
    # tail). Optional and honest: strict pixel IoU on a thin class is low by
    # nature, so the card leads with the tolerance-band recall/precision and states
    # the geometry + on-device caveats. Absent baseline => no lane card (no fake).
    lane_b = load_baseline(CAPABILITY_LANE_BASELINE)
    lane_section = None
    if lane_b:
        lm = lane_b.get("metrics", {}) if isinstance(lane_b.get("metrics"), dict) else {}
        lane_section = {
            "mean_recall_tol": _f(lm, "mean_recall_tol"),
            "mean_precision_tol": _f(lm, "mean_precision_tol"),
            "mean_iou": _f(lm, "mean_iou"),
            "lane_miss_frames": _i(lm, "lane_miss_frames"),
            "lane_frames": _i(lm, "lane_frames"),
            "tol": _i(lm, "tol"),
            "scored": _i(lm, "scored"),
            "on_device": bool(lane_b.get("on_device", False)),
        }

    # Obstacle capability (YOLO boxes vs CamVid obstacle pixels). Optional and
    # honest: this is a coverage PROXY (CamVid has no GT boxes), so the card says so.
    obstacle_b = load_baseline(CAPABILITY_OBSTACLE_BASELINE)
    obstacle_section = None
    if obstacle_b:
        om = obstacle_b.get("metrics", {}) if isinstance(obstacle_b.get("metrics"), dict) else {}
        obstacle_section = {
            "coverage_recall": _f(om, "mean_coverage_recall"),
            "box_precision": _f(om, "mean_box_precision"),
            "obstacle_miss_frames": _i(om, "obstacle_miss_frames"),
            "obstacle_frames": _i(om, "obstacle_frames"),
            "scored": _i(om, "scored"),
        }

    safe = region_false_go == 0
    if iou >= 0.85:
        level = "优"
    elif iou >= 0.70:
        level = "中上"
    elif iou >= 0.50:
        level = "中"
    else:
        level = "偏弱"
    miss_ratio = (region_miss / scored) if scored else 0.0
    conservative = recall < 0.90 or miss_ratio > 0.05

    if safe and conservative:
        summary = f"当前「{level}·偏保守但安全」：敢报的基本准，但该说能走的地方它常常没说。"
    elif safe:
        summary = f"当前「{level}·稳」：可走区域准，且不冒进。"
    else:
        summary = f"当前「{level}·有冒进风险」：存在把不可走当可走的帧，需优先修安全侧。"

    # Weakness → next step (owner is 全麦 for model recall).
    weakness_bits = []
    if region_miss:
        weakness_bits.append(f"{region_miss}/{scored} 帧漏可走（召回 {recall:.0%}）")
    if not safe:
        weakness_bits.append(f"区域误判可走 {region_false_go} 帧")
    if weakness_bits:
        weakness = "，".join(weakness_bits) + "。建议用真机帧微调提升召回（全麦），Phase 2 已在留出集证明可将漏报降到 0。"
    else:
        weakness = "无明显短板；可扩大到更多真实场景（室内、雨天、夜间）验证泛化。"

    # Trend vs committed baseline (only if a live report exists).
    report = _capability_report_metrics()
    trend = {"status": "first", "text": "已建立门禁基线：下次「iPhone 真身评估」若退步会被门禁拦住。"}
    if isinstance(report, dict):
        r_live = report.get("region") if isinstance(report.get("region"), dict) else {}
        d_iou = _f(r_live, "mean_iou") - iou
        eps = 1e-4
        if abs(d_iou) < eps:
            trend = {"status": "flat", "text": "最近一次评估与门禁基线持平：当前即基线，没有退步。"}
        else:
            worse = d_iou < -eps
            trend = {
                "status": "down" if worse else "up",
                "text": (
                    ("⚠ 相比基线退步：" if worse else "↑ 相比基线变好：")
                    + f"可走区域 IoU {'+' if d_iou >= 0 else ''}{d_iou:.03f}"
                ),
            }

    return {
        "available": True,
        "level": level,
        "safe": safe,
        "summary": summary,
        "weakness": weakness,
        "trend": trend,
        "scored": scored,
        "source": str(region_b.get("source") or ""),
        "region": {
            "mean_iou": iou,
            "mean_precision": precision,
            "mean_recall": recall,
            "region_false_go_frames": region_false_go,
            "region_miss_frames": region_miss,
            "scored": scored,
        },
        "role": role_section,
        "drive": drive_section,
        "lane": lane_section,
        "obstacle": obstacle_section,
    }


def _capability_scorecard_html() -> str:
    """Render the capability scorecard for the diagnostics landing page."""
    snap = _capability_snapshot()
    if not snap.get("available"):
        return (
            "<div class='hero'>"
            "<h1>iPhone 本地感知能力总览</h1>"
            "<div class='status error'><b>尚无能力基线</b>"
            f"<p class='hint'>{html.escape(str(snap.get('reason', '')))}</p>"
            "<p><a href='/diagnostics/datasets/ui'>去跑一次 iPhone 真身评估 →</a></p></div></div>"
        )

    region = snap["region"]
    trend = snap["trend"]
    safe = snap["safe"]

    def _pct(v: float) -> str:
        return f"{v * 100:.0f}%"

    region_card = (
        "<div class='card'>"
        "<h2>看得准 · 可走区域</h2>"
        f"<p style='font-size:2rem;font-weight:800'>IoU {region['mean_iou']:.02f}</p>"
        f"<p class='muted'>精度 {_pct(region['mean_precision'])} · 召回 {_pct(region['mean_recall'])}</p>"
        f"<p class='muted'>误判可走 {region['region_false_go_frames']} 帧 · 漏可走 {region['region_miss_frames']}/{region['scored']} 帧</p>"
        "<p class='explain'>iPhone 分割出的可走区域与 CamVid 真值的像素重合度。精度高=不乱说能走。</p>"
        "</div>"
    )
    safe_border = "#30d158" if safe else "#ff453a"
    safe_head = "宁可保守不冒进" if safe else "存在冒进帧，需优先修"
    safe_big = "0 冒进帧" if safe else f"{region['region_false_go_frames']} 冒进帧"
    safe_card = (
        f"<div class='card' style='border-color:{safe_border}'>"
        "<h2>安全侧 · 会不会冒进</h2>"
        f"<p style='font-size:2rem;font-weight:800;color:{safe_border}'>{safe_big}</p>"
        f"<p class='muted'>{html.escape(safe_head)}</p>"
        "<p class='explain'>「冒进」=把不可走当可走。CamVid 线级真值已退役，只看可走区域像素。</p>"
        "</div>"
    )

    # Lane-marking capability card. Leads with tolerance-band recall/precision
    # (strict pixel IoU on a thin class is harsh); states honestly that only lane
    # PIXELS are modelled (no geometry) and whether it is on-device yet.
    lane = snap.get("lane")
    lane_card = ""
    if lane:
        lane_dev = "已上设备" if lane.get("on_device") else "尚未捆绑（仅离线）"
        lane_card = (
            "<div class='card'>"
            "<h2>车道线 · 找得到吗</h2>"
            f"<p style='font-size:2rem;font-weight:800'>召回 {_pct(lane['mean_recall_tol'])}</p>"
            f"<p class='muted'>精度 {_pct(lane['mean_precision_tol'])}（容差 {lane['tol']}px）· "
            f"漏车道 {lane['lane_miss_frames']}/{lane['lane_frames']} 帧</p>"
            f"<p class='muted'>严格像素 IoU {lane['mean_iou']:.02f}（细线天然偏低，看容差带）· 留出集 {lane['scored']} 帧</p>"
            "<p class='explain'>专用二值车道分割模型在 CamVid 留出集(Seq05V)上的表现。"
            f"<b>只分割车道像素、不建模车道几何/自车道</b>；设备端<b>{html.escape(lane_dev)}</b>。</p>"
            "</div>"
        )

    # Obstacle capability card. Safety framing: coverage recall = of CamVid obstacle
    # pixels, how many the YOLO boxes cover (low = it does NOT see obstacles). Honest
    # that this is a coverage proxy, not detection mAP (CamVid has no GT boxes).
    obstacle = snap.get("obstacle")
    obstacle_card = ""
    if obstacle:
        rec = obstacle["coverage_recall"]
        obs_border = "#30d158" if rec >= 0.70 else ("#ff9f0a" if rec >= 0.40 else "#ff453a")
        obstacle_card = (
            f"<div class='card' style='border-color:{obs_border}'>"
            "<h2>障碍物 · 看得见吗</h2>"
            f"<p style='font-size:2rem;font-weight:800;color:{obs_border}'>覆盖召回 {_pct(rec)}</p>"
            f"<p class='muted'>落点精度 {_pct(obstacle['box_precision'])} · "
            f"漏障碍 {obstacle['obstacle_miss_frames']}/{obstacle['obstacle_frames']} 帧</p>"
            "<p class='explain'>YOLO 检测框覆盖到 CamVid 障碍像素(车/人/自行车等)的比例。"
            "<b>覆盖代理指标、非检测 mAP</b>(CamVid 无真值框);低=没看见该看见的障碍。</p>"
            "</div>"
        )

    trend_border = {"down": "#ff453a", "up": "#30d158", "flat": "#3a3a3c", "first": "#2f6f9f"}.get(trend["status"], "#3a3a3c")
    scored = snap["scored"]
    hero = (
        "<div class='hero'>"
        "<h1>iPhone 本地感知能力总览</h1>"
        f"<p class='hint' style='font-size:1.05rem'>{html.escape(snap['summary'])}</p>"
        f"<p><span class='pill'>数据集 CamVid</span><span class='pill'>{scored} 帧</span>"
        "<span class='pill'>离线 harness</span><span class='pill'>无深度 · 相机分支</span></p>"
        "<p class='explain'>诚实边界：这是 CamVid 户外街景上的离线数值，非真机体验；室内/雨夜等其它场景不保证一样。</p>"
        "</div>"
    )
    trend_card = (
        f"<div class='card' style='border-color:{trend_border}'>"
        "<h2>变好还是变差</h2>"
        f"<p>{html.escape(trend['text'])}</p>"
        "<p class='explain'>门禁受 <code>region</code> 可走区域基线保护；CamVid 线级真值与三区状态已退役。</p>"
        "</div>"
    )
    weakness_card = (
        "<div class='card'>"
        "<h2>下一步修哪</h2>"
        f"<p>{html.escape(snap['weakness'])}</p>"
        "</div>"
    )

    # Role-conditioned (walk) red-line: how often the device would route a walker
    # onto the road. Shown only when a role baseline exists; measured against a
    # DIFFERENT truth (sidewalk=walkable) so it carries its own honest framing.
    role = snap.get("role")
    role_card = ""
    if role:
        ras = role["road_as_primary_rate"]
        over_redline = ras > ROLE_ROAD_AS_PRIMARY_REDLINE
        role_border = "#ff453a" if over_redline else "#30d158"
        role_card = (
            f"<div class='card' style='border-color:{role_border}'>"
            "<h2>行人角色 · 可走区谁说了算</h2>"
            f"<p style='font-size:2rem;font-weight:800;color:{role_border}'>马路误当人行道 {_pct(ras)}</p>"
            f"<p class='muted'>{role['road_as_primary_frames']}/{role['scored']} 帧会把行人引到马路上</p>"
            f"<p class='muted'>人行道召回 {_pct(role['primary_recall'])} · 车道线覆盖 {_pct(role['lane_coverage'])} · 障碍重叠 {role['obstacle_overlap_frames']} 帧</p>"
            "<p class='explain'>按行人角色真值（人行道=可走、马路=慎行）衡量：当前设备仍是二值「可走区」，"
            "<b>没有人行道/马路边界与车道线通道</b>，所以大量把马路当人行道。修复=<b>T2 多类分割</b>"
            "（CamVid 微调 → Core ML N 类）+ 专用车道模型。</p>"
            "</div>"
        )

    # Driving-role card, right beside the walk card, to make the "区分人车" contrast
    # unmistakable: the SAME device grid is near-perfect for a driver (road recall
    # high, ~0 sidewalk-as-road frames) yet dangerous for a walker (above).
    drive = snap.get("drive")
    drive_card = ""
    if drive:
        d_over = drive["sidewalk_as_road_frames"] > 0
        d_border = "#ff453a" if d_over else "#30d158"
        drive_card = (
            f"<div class='card' style='border-color:{d_border}'>"
            "<h2>驾驶角色 · 可走区谁说了算</h2>"
            f"<p style='font-size:2rem;font-weight:800;color:{d_border}'>人行道误当车道 {_pct(drive['sidewalk_as_road_rate'])}</p>"
            f"<p class='muted'>{drive['sidewalk_as_road_frames']}/{drive['scored']} 帧把车引上人行道 · 道路召回 {_pct(drive['road_recall'])}</p>"
            f"<p class='muted'>越界到人行道/行人区 {drive['obstacle_overlap_frames']} 帧</p>"
            "<p class='explain'>同一套二值可走区，按<b>驾驶</b>真值（马路=可走、人行道=禁区）衡量：对驾驶者"
            "<b>近乎安全</b>——正说明<b>可通行区域必须区分人车</b>，一份预测对司机安全却会把行人引上马路。</p>"
            "</div>"
        )

    return (
        hero
        + "<div class='grid'>"
        + region_card
        + lane_card
        + obstacle_card
        + safe_card
        + "</div>"
        + ("<div class='grid'>" + role_card + drive_card + "</div>" if (role_card or drive_card) else "")
        + trend_card
        + weakness_card
    )


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
    scorecard = _capability_scorecard_html()
    # Primary drill-down: the pages that actually answer "how good / where does it
    # fail". Kept one tap away, not spread across the landing page.
    drilldown = """
<div class='grid'>
  <div class='card'><h2>逐帧识别效果</h2><p class='muted'>逐帧看 iPhone 感知的可走区域 / 引导线 与 CamVid 真值叠加，按漏报/误挡筛选。</p><p><a href='/diagnostics/datasets/ui'>打开数据集评估 →</a></p></div>
  <div class='card'><h2>TwinLiteNet 预览</h2><p class='muted'>12 帧 CamVid test：红可行驶区 / 绿车道线 / 蓝引导线。只吃 RGB，不是 App 默认。</p><p><a href='/diagnostics/twinlite/ui'>打开 TwinLiteNet 画廊 →</a></p></div>
  <div class='card'><h2>闭环 case</h2><p class='muted'>评估里的失败帧自动聚类成可跟踪、能重开的 case（借鉴 DCL 统一载体 + 生命周期）。</p><p><a href='/diagnostics/cases/ui'>打开 case 列表 →</a></p></div>
  <div class='card'><h2>感知配置（OTA）</h2><p class='muted'>查看/发布下发到 iPhone 的感知参数版本。</p><p><a href='/diagnostics/perception-config/ui'>打开感知配置 →</a></p></div>
</div>
"""
    about = """
<details>
  <summary>关于这个平台 · 真机诊断 Sessions</summary>
  <p class='hint'>从真机诊断帧、开源数据集、本地感知层、Mac 后端 Qwen 到评估报告的一站式实验入口。普通用户不会看到这个页面。</p>
  <p class='muted'>1. 真机诊断 Sessions：iPhone 上传的帧、metadata、本地模型输出和 path guidance。
  2. 引导层可视化：把 LocalPathGuidanceSignal 叠加到图片上检查。
  3. 评估报告：自动发现 in-flight、误报、漏报、depth/segmentation 能力缺口。</p>
</details>
"""
    body = (
        scorecard
        + "<h2>下钻</h2>"
        + drilldown
        + about
        + "<details><summary>Sessions（真机诊断记录）</summary>"
        + script
        + ("".join(cards) or "<p class='muted'>暂无 session。</p>")
        + "</details>"
    )
    return _html_page("iPhone 本地感知能力总览", body)


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


def _svg_corridor(rect: dict, blocked: bool) -> str:
    color = "#ff453a" if blocked else "#64d2ff"
    try:
        min_y = float(rect.get("y", 0))
        max_y = min_y + float(rect.get("height", 0))
    except (TypeError, ValueError):
        return ""
    bottom_y = (1 - min_y) * 100
    top_y = (1 - min(max_y, 0.62)) * 100
    points = f"30,{bottom_y:.2f} 42,{top_y:.2f} 58,{top_y:.2f} 70,{bottom_y:.2f}"
    opacity = 0.20 if blocked else 0.08
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
    corridor = path_guidance.get("guidance_corridor")
    blocked = path_guidance.get("blocked_regions") if isinstance(path_guidance.get("blocked_regions"), list) else []
    uncertain = path_guidance.get("uncertain_regions") if isinstance(path_guidance.get("uncertain_regions"), list) else []
    if corridor and (blocked or uncertain):
        parts.append(_svg_corridor(corridor, bool(blocked)))
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


def _is_camvid_test_manifest(path: Path) -> bool:
    name = path.name
    return name.startswith("camvid-manifest") and name.endswith("-test.jsonl")


def _is_camvid_full_manifest(path: Path) -> bool:
    return path.name in {
        "camvid-manifest.jsonl",
        "camvid-manifest-walk.jsonl",
        "camvid-manifest-drive.jsonl",
    }


def _truth_dataset_card(path: Path, *, badge: str = "", hint: str = "") -> str:
    safe_name = html.escape(path.name)
    encoded = html.escape(str(path))
    links = (
        f"<a href='/diagnostics/datasets/manifest/ui?manifest={encoded}'>浏览</a> · "
        f"<a href='/diagnostics/datasets/evaluate/ui?manifest={encoded}'>服务器代理评估</a> · "
        f"<a href='/diagnostics/datasets/ios-harness/ui?manifest={encoded}'>iPhone 真身评估</a>"
    )
    badge_html = f" {badge}" if badge else ""
    hint_html = f"<p class='hint'>{hint}</p>" if hint else ""
    return (
        f"<div class='card'><h2>{safe_name}{badge_html}</h2>"
        f"<p class='muted'>{html.escape(str(path))}</p>"
        f"{hint_html}"
        f"<p>{links}</p></div>"
    )


def _read_first_json_row(path: Path) -> Optional[dict]:
    """First non-empty JSONL row as a dict, or None if empty/unparseable."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    return row if isinstance(row, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _manifest_runnable_reason(manifest_path: Path) -> Optional[str]:
    """None if this is a ground-truth dataset manifest the harness can run (rows carry
    image paths). Otherwise an actionable Chinese reason.

    Guards the common footgun of pointing the harness at its own PREDICTIONS output
    (``*-ios-harness.jsonl``: frame_id + prediction, no image), which would otherwise
    report a cryptic ``missing_image=N`` for every frame instead of failing loudly.
    """
    row = _read_first_json_row(manifest_path)
    if isinstance(row, dict) and ("image_path" in row or "image" in row):
        return None
    if row is None:
        return f"“{manifest_path.name}”为空或无法解析为 JSONL，无法作为数据集运行。"
    if "prediction" in row or "guidance_path" in row or "traversable_grid" in row:
        return (
            f"“{manifest_path.name}”是 harness 的预测结果文件（只有预测、没有图片路径），"
            "不是数据集 manifest。请改用真值数据集，例如 docs/datasets/camvid-manifest.jsonl。"
        )
    return (
        f"“{manifest_path.name}”缺少图片字段（image_path / image），"
        "无法作为数据集 manifest 运行真身感知。"
    )


@router.get("/datasets/ui", response_class=HTMLResponse)
def datasets_ui():
    # Split the flat .jsonl list into two clearly-labelled groups so the page stops
    # dumping internal plumbing on the user:
    #   - 真值数据集 (runnable): the answer keys the harness scores against.
    #   - 预测结果/派生文件 (NOT runnable): harness outputs; results belong on the
    #     scorecard, so these are collapsed under a "developer" section.
    iteration_cards: list[str] = []
    full_cards: list[str] = []
    other_cards: list[str] = []
    pred_cards: list[str] = []
    for path in _dataset_manifest_candidates():
        runnable = _manifest_runnable_reason(path) is None
        if not runnable:
            pred_cards.append(
                f"<div class='card'><h2>{html.escape(path.name)}</h2>"
                f"<p class='muted'>{html.escape(str(path))}</p>"
                f"<p><a href='/diagnostics/datasets/manifest/ui?manifest={html.escape(str(path))}'>浏览原始行</a></p></div>"
            )
            continue
        if _is_camvid_test_manifest(path):
            role = "机动车" if "drive" in path.name else "行人" if "walk" in path.name else "混合可走"
            iteration_cards.append(
                _truth_dataset_card(
                    path,
                    badge="<span class='pill' style='border:1px solid #30d158;color:#30d158'>推荐迭代</span>",
                    hint=f"CamVid 四种街道场景各 3 张，共 12 帧 · {role}。改算法先跑这个，大约 1 分钟出结果。",
                )
            )
        elif _is_camvid_full_manifest(path):
            full_cards.append(
                _truth_dataset_card(
                    path,
                    hint="全量 701 帧，发布前回归用。日常改算法请用上面的 test 集。",
                )
            )
        else:
            other_cards.append(_truth_dataset_card(path))

    truth_blocks = []
    if iteration_cards:
        truth_blocks.append(
            "<h2>日常迭代（推荐）</h2>"
            "<p class='hint'>从 CamVid 四段行车录像各抽 3 张，用来快速看车道线、可通行区和障碍物效果。全量 701 太慢，不适合每次改一点就重跑。</p>"
            + "".join(iteration_cards)
        )
    if full_cards:
        truth_blocks.append(
            "<details><summary>全量回归（701 帧，Intel Mac TwinLiteNet 实验大约 10–15 分钟）</summary>"
            "<p class='hint'>发布前或要数字基线时再跑。带角色后缀的是同一批帧按行人/机动车重新判定的可通行真值。"
            "CamVid 角色 IoU 的 mc5 多类分割已退出日常 harness，需手动加 <code>--seg-model</code> 与 "
            "<code>road_backend=mc5</code>（Intel Mac 约 35 分钟）。</p>"
            + "".join(full_cards)
            + "</details>"
        )
    if other_cards:
        truth_blocks.append("<h2>其他真值数据集</h2>" + "".join(other_cards))
    truth_section = "".join(truth_blocks) or (
        "<h2>真值数据集</h2>"
        "<p class='muted'>暂无真值数据集。示例：docs/datasets/camvid-manifest-drive-test.jsonl</p>"
    )
    pred_section = ""
    if pred_cards:
        pred_section = (
            "<details><summary>预测结果 / 派生文件（开发调试用，"
            f"{len(pred_cards)} 个）</summary>"
            "<p class='hint'>这些是 iPhone 感知模型跑出来的<strong>答卷</strong>，不是数据集，不能再拿去评测。"
            "想看它们的得分请回 <a href='/diagnostics/ui'>感知能力总览</a>，这里仅供开发排查原始行。</p>"
            + "".join(pred_cards)
            + "</details>"
        )
    twinlite_card = (
        "<div class='card'><h2>TwinLiteNet 预览（不是 App 默认）</h2>"
        "<p class='hint'>同一套 12 帧 test split，只吃 RGB：红=可行驶区域，绿=车道线，蓝=引导线。"
        "第一次打开大约半分钟出齐缓存。</p>"
        "<p><a href='/diagnostics/twinlite/ui'>打开 12 帧画廊 →</a></p></div>"
    )
    body = (
        "<p><a href='/diagnostics/ui'>← 感知能力总览</a></p>"
        "<h1>开源/本地数据集评估</h1>"
        + twinlite_card
        + "<p><a href='/diagnostics/datasets/create-open/ui'>接入开源数据集</a> · <a href='/diagnostics/datasets/create/ui'>从图片+mask目录创建 manifest</a> · <a href='/diagnostics/perception-config/ui'>感知配置（OTA）</a></p>"
        "<p class='hint'>开源数据集先使用本地已下载数据；平台不自动下载大文件，也不绕过数据集 license。生成的 path manifest 放到 docs/datasets/ 或 VQASEE_DATASET_MANIFEST_DIR 后可在这里评估。</p>"
        + truth_section
        + pred_section
    )
    return _html_page("数据集评估", body)


def _allowed_local_roots() -> list[Path]:
    roots = [Path.cwd().resolve(), Path("/private/tmp").resolve(), Path("/tmp").resolve()]
    configured = os.getenv("VQASEE_DATASET_ROOT", "").strip()
    if configured:
        roots.append(Path(configured).expanduser().resolve())
    # Always allow wherever datasets actually live (durable default is
    # <repo>/dataset). Deriving this from the same resolver keeps the
    # file-serving allowlist in lockstep with the download target, so moving
    # the root can't silently break image serving.
    roots.append(_open_dataset_root())
    return roots


def _safe_local_file(path_text: str) -> Path:
    found = resolve_dataset_file(
        path_text,
        dataset_root=_open_dataset_root(),
        repo=_repo_root(),
    )
    if found is None:
        raise HTTPException(status_code=404, detail="file_not_found")
    if not any(root == found or root in found.parents for root in _allowed_local_roots()):
        raise HTTPException(status_code=403, detail="file_not_allowed")
    return found


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


@router.get("/camvid-mask")
def camvid_mask(label: str, classes: str = "walk", w: int = 560):
    """Render the CamVid-derived traversable region as a translucent green PNG.

    Lets the user visually verify the ground-truth guidance line: green = pixels
    that count as traversable under the chosen classes, everything else fully
    transparent. Reuses the SAME mask builder that generates the GT line, so the
    tint you see is exactly what the green line was traced from — not a second,
    independently-drifting drawing. The label path goes through the same
    allowlist as every other served file (no arbitrary local read)."""
    label_path = _safe_local_file(label)
    try:
        colors = camvid_traversable_colors(classes)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"bad_traversable_classes: {exc}") from exc
    try:
        mask = _camvid_traversability_mask(label_path, colors)
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"mask_render_failed: {exc}") from exc

    height, width = mask.shape[:2]
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    # Apple green (#30d158) at ~38% alpha where traversable; transparent elsewhere.
    rgba[mask] = (48, 209, 88, 96)
    image = Image.fromarray(rgba)  # (H, W, 4) uint8 -> RGBA inferred
    if w and w > 0:
        max_width = min(w, 1600)
        if image.width > max_width:
            new_height = max(1, round(image.height * max_width / image.width))
            # NEAREST keeps the mask edges faithful to the labeled pixels instead
            # of inventing soft, misleading coverage at the boundary.
            image = image.resize((max_width, new_height), resample=Image.NEAREST)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


def _twinlite_catalog() -> dict:
    from app.camvid_scene_sample import load_camvid_test_catalog

    return load_camvid_test_catalog(_repo_root())


def _twinlite_allowed_stems() -> set[str]:
    catalog = _twinlite_catalog()
    stems = [str(stem) for stem in (catalog.get("stems") or []) if stem]
    if stems:
        return set(stems)
    return {
        str(sample.get("stem"))
        for scene in (catalog.get("scenes") or [])
        if isinstance(scene, dict)
        for sample in (scene.get("samples") or [])
        if isinstance(sample, dict) and sample.get("stem")
    }


def _twinlite_rgb(stem: str) -> np.ndarray:
    from app.camvid_scene_sample import camvid_rgb_dir

    path = camvid_rgb_dir() / f"{stem}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="rgb_not_found")
    safe = _safe_local_file(str(path))
    return np.asarray(Image.open(safe).convert("RGB"))


def _twinlite_overlay_file(stem: str) -> Path:
    from app.twinlite_preview import write_cached_overlay

    if stem not in _twinlite_allowed_stems():
        raise HTTPException(status_code=404, detail="stem_not_in_test_split")
    try:
        rgb = _twinlite_rgb(stem)
        return write_cached_overlay(stem, rgb)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"twinlite_overlay_failed: {exc}") from exc


@router.get("/twinlite/status")
def twinlite_status():
    from app.twinlite_net import LAST_ERROR, default_twinlite_repo, default_twinlite_weights
    from app.twinlite_preview import cached_overlay_path

    stems = sorted(_twinlite_allowed_stems())
    cached = [stem for stem in stems if cached_overlay_path(stem).is_file()]
    weights = default_twinlite_weights()
    repo = default_twinlite_repo()
    return {
        "weights": weights is not None,
        "weights_path": str(weights) if weights is not None else None,
        "repo": repo is not None,
        "stems": stems,
        "cached": cached,
        "last_error": LAST_ERROR,
        "app_default": False,
        "note": "诊断台预览，不是 iPhone 默认路径。本机 CPU 约 2.4 秒/帧。",
    }


@router.get("/twinlite/frame")
def twinlite_frame(stem: str, w: int = 0):
    file_path = _twinlite_overlay_file(stem)
    if w and w > 0:
        max_width = min(int(w), 1600)
        try:
            with Image.open(file_path) as image:
                image = image.convert("RGB")
                image.thumbnail((max_width, max_width * 4))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=82)
        except OSError as exc:
            raise HTTPException(status_code=422, detail=f"thumbnail_failed: {exc}") from exc
        return Response(content=buffer.getvalue(), media_type="image/jpeg")
    return FileResponse(file_path, media_type="image/jpeg")


@router.get("/twinlite/ui", response_class=HTMLResponse)
def twinlite_ui():
    catalog = _twinlite_catalog()
    scenes = catalog.get("scenes") or []
    sections: list[str] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        samples = scene.get("samples") or []
        cards: list[str] = []
        for sample in samples:
            if not isinstance(sample, dict) or not sample.get("stem"):
                continue
            stem = str(sample["stem"])
            encoded = html.escape(stem)
            situation = html.escape(str(sample.get("situation_label") or sample.get("situation") or ""))
            cards.append(
                "<div class='card'>"
                f"<h3>{encoded}</h3>"
                f"<p class='muted'>{situation}</p>"
                f"<img data-src='/diagnostics/twinlite/frame?stem={encoded}&w=720' alt='{encoded}'>"
                "</div>"
            )
        if not cards:
            continue
        label = html.escape(str(scene.get("label") or scene.get("id") or ""))
        summary = html.escape(str(scene.get("summary") or ""))
        sections.append(
            f"<h2>{html.escape(str(scene.get('id') or ''))} · {label}</h2>"
            f"<p class='hint'>{summary}</p>"
            f"<div class='gallery'>{''.join(cards)}</div>"
        )
    missing = ""
    if not sections:
        missing = "<p class='muted'>没有 camvid-test-scenes.json，或里面没有帧。</p>"
    body = (
        "<p><a href='/diagnostics/ui'>← 感知能力总览</a> · "
        "<a href='/diagnostics/datasets/ui'>数据集评估</a></p>"
        "<h1>TwinLiteNet 城市街景预览</h1>"
        "<div class='callout'>"
        "<p><strong>不是 iPhone 默认。</strong>只吃 RGB，不读 CamVid 车道标注。"
        "红=预测可行驶区域，绿=预测车道线，蓝=占用车道引导线。</p>"
        "<p class='muted'>本机 CPU 大约 2.4 秒/帧。第一次打开会<strong>一张一张</strong>生成，避免 12 路同时推理把机器打满。之后走缓存。</p>"
        "<p class='muted'>看图门：绿线是不是司机看见的边，蓝线有没有钩子/喷到对向车道。</p>"
        "</div>"
        + missing
        + "".join(sections)
        + """<script>
(async function () {
  const imgs = [...document.querySelectorAll('img[data-src]')];
  for (const img of imgs) {
    img.src = img.dataset.src;
    await new Promise((resolve) => {
      img.onload = resolve;
      img.onerror = resolve;
    });
  }
})();
</script>"""
    )
    return _html_page("TwinLiteNet 预览", body)


def _traversable_grid_to_mask(grid: dict) -> "np.ndarray | None":
    """Validate a harness `traversable_grid` and return a bool (rows, cols) mask,
    row 0 = TOP of the image. None if the payload is malformed — the caller then
    states "no perceived region" explicitly rather than drawing garbage."""
    if not isinstance(grid, dict):
        return None
    try:
        cols = int(grid.get("cols", 0))
        rows = int(grid.get("rows", 0))
    except (TypeError, ValueError):
        return None
    cells = grid.get("cells")
    if cols <= 0 or rows <= 0 or not isinstance(cells, list) or len(cells) != cols * rows:
        return None
    try:
        arr = np.asarray(cells, dtype=np.int16).reshape(rows, cols)
    except (TypeError, ValueError):
        return None
    return arr > 0


def _traversable_grid_png_datauri(grid: dict) -> Optional[str]:
    """Render the iPhone-perceived walkable region (harness `traversable_grid`) as
    a translucent green PNG, inlined as a data URI so the per-frame page needs no
    extra request. Row 0 = top, so it overlays aligned with the frame image and the
    CamVid GT mask. Blocky by design (`image-rendering: pixelated`): the honest
    coarse resolution of what the on-device segmentation actually perceives."""
    mask = _traversable_grid_to_mask(grid)
    if mask is None:
        return None
    rows, cols = mask.shape
    rgba = np.zeros((rows, cols, 4), dtype=np.uint8)
    # Apple green (#30d158) at ~43% alpha where the device perceives traversable.
    rgba[mask] = (48, 209, 88, 110)
    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _lane_grid_png_datauri(grid: dict, rgba: tuple[int, int, int, int]) -> Optional[str]:
    """Render a lane-marking grid (harness `lane_grid` prediction, or the manifest
    `lane_grid_fine` ground truth) as a translucent overlay in the given colour.
    Reuses the traversable-grid validator (same {cols, rows, cells} wire shape),
    so a malformed payload yields None → caller draws nothing rather than garbage.
    Fine lane grid (128x96, any-pixel-hit) keeps thin markings visible."""
    mask = _traversable_grid_to_mask(grid)
    if mask is None:
        return None
    rows, cols = mask.shape
    out = np.zeros((rows, cols, 4), dtype=np.uint8)
    out[mask] = rgba
    buffer = io.BytesIO()
    Image.fromarray(out).save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


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
  <p class='hint'>推荐先点这个。平台会从 GitHub 下载公开 CamVid 镜像到仓库 <code>dataset/camvid</code>（可用环境变量 <code>VQASEE_DATASET_ROOT</code> 覆盖），读取道路/人行道语义标签，生成 VQASee path-guidance manifest。若目录被清空会自动重新下载。</p>
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
    status.innerHTML = `正在连接 GitHub 并下载 CamVid… 已等待 ${seconds} 秒。<br><span class="muted">完整包较大，可能需要几分钟。网络慢时可以先打开本地演示或稍后重试。</span>`;
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
    # Default is <repo>/dataset so committed manifests never bake in another
    # machine's home directory. VQASEE_DATASET_ROOT still overrides.
    return open_dataset_root()


def _dir_has_images(directory: Path | None) -> bool:
    """True only if the dir exists AND holds at least one image file.

    An empty directory (e.g. after macOS purged /tmp but left the folder) must
    NOT count as "downloaded": otherwise we silently skip re-download and then
    fail every frame with a misleading decode error. Treat empty == absent.
    """
    if directory is None or not directory.is_dir():
        return False
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            return True
    return False


def _detect_camvid_dirs() -> tuple[Path | None, Path | None]:
    """Find an already-downloaded CamVid's RGB/label dirs, tolerant of nesting.

    Lets the UI auto-fill Step 2 instead of asking the user to type paths that
    depend on the archive's internal folder (e.g. CamVid-main/CamVid_RGB).
    """
    root = _open_dataset_root() / "camvid"
    if not root.is_dir():
        return None, None
    rgb = _find_dataset_dir(root, "CamVid_RGB")
    lbl = _find_dataset_dir(root, "CamVid_Label")
    # Empty (purged) dirs must not auto-fill Step 2 as if they were ready.
    return (rgb if _dir_has_images(rgb) else None), (lbl if _dir_has_images(lbl) else None)


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


def _normalize_camvid_layout(root: Path) -> None:
    """Accept GitHub (CamVid_RGB/Label) and official UCL (701_StillsRaw_full + *_L.png)."""
    rgb = _find_dataset_dir(root, "CamVid_RGB")
    raw = _find_dataset_dir(root, "701_StillsRaw_full")
    if not _dir_has_images(rgb) and _dir_has_images(raw) and raw is not None:
        target = root / "CamVid_RGB"
        if target.exists() and target.resolve() != raw.resolve():
            shutil.rmtree(target)
        if raw.resolve() != target.resolve():
            raw.rename(target)

    lbl = _find_dataset_dir(root, "CamVid_Label")
    if _dir_has_images(lbl):
        return
    target = root / "CamVid_Label"
    target.mkdir(parents=True, exist_ok=True)
    for png in root.rglob("*_L.png"):
        if not png.is_file():
            continue
        dest = target / png.name
        if png.resolve() == dest.resolve():
            continue
        if dest.exists():
            continue
        png.replace(dest)


def _download_official_camvid(root: Path) -> None:
    """Download the original Cambridge/UCL CamVid stills + labels (not GitHub)."""
    rgb_zip = root / "701_StillsRaw_full.zip"
    lbl_zip = root / "LabeledApproved_full.zip"
    _download_url_to_file(
        "http://web4.cs.ucl.ac.uk/staff/g.brostow/MotionSegRecData/files/701_StillsRaw_full.zip",
        rgb_zip,
        timeout_seconds=1800,
    )
    _download_url_to_file(
        "http://web4.cs.ucl.ac.uk/staff/g.brostow/MotionSegRecData/data/LabeledApproved_full.zip",
        lbl_zip,
        timeout_seconds=600,
    )
    _extract_zip_flat(rgb_zip, root)
    labels_tmp = root / "_labels_extract"
    if labels_tmp.exists():
        shutil.rmtree(labels_tmp)
    labels_tmp.mkdir(parents=True)
    _extract_zip_flat(lbl_zip, labels_tmp)
    rgb_zip.unlink(missing_ok=True)
    lbl_zip.unlink(missing_ok=True)
    _normalize_camvid_layout(root)
    shutil.rmtree(labels_tmp, ignore_errors=True)



def _download_url_to_file(url: str, output_path: Path, *, timeout_seconds: int = 300) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "VQASee-diagnostics/1.0"})
    last_error: Exception | None = None
    # Try the process proxy first, then a direct connection. Local HTTP proxies
    # on this machine have previously truncated GitHub/Homebrew payloads.
    for disable_proxy in (False, True):
        try:
            if disable_proxy:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                response_cm = opener.open(request, timeout=timeout_seconds)
            else:
                response_cm = urllib.request.urlopen(request, timeout=timeout_seconds)
            with response_cm as response, output_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            if output_path.stat().st_size == 0:
                raise OSError(f"empty download: {url}")
            return
        except Exception as exc:
            last_error = exc
            output_path.unlink(missing_ok=True)
    raise last_error or OSError(f"download_failed: {url}")


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
        "https://ghproxy.net/https://github.com/lih627/CamVid/archive/refs/heads/main.zip",
        "https://mirror.ghproxy.com/https://github.com/lih627/CamVid/archive/refs/heads/main.zip",
    ]
    # Re-download when the images are missing OR the dir exists but is empty
    # (e.g. macOS purged /tmp). Checking presence-of-files, not just the folder,
    # is what lets the platform self-heal instead of silently skipping.
    if not _dir_has_images(_find_dataset_dir(root, "CamVid_RGB")) or not _dir_has_images(
        _find_dataset_dir(root, "CamVid_Label")
    ):
        last_error: Exception | None = None
        downloaded = False
        for url in urls:
            try:
                _download_url_to_file(url, zip_path, timeout_seconds=300)
                _extract_zip_flat(zip_path, root)
                _normalize_camvid_layout(root)
                downloaded = True
                break
            except Exception as exc:  # pragma: no cover - network failures are environment-specific.
                last_error = exc
            finally:
                zip_path.unlink(missing_ok=True)
        if not downloaded:
            try:
                _download_official_camvid(root)
                downloaded = True
            except Exception as exc:  # pragma: no cover - network failures are environment-specific.
                last_error = exc
        if not downloaded:
            raise HTTPException(status_code=502, detail=f"download_failed: {last_error}")
    images_dir = _find_dataset_dir(root, "CamVid_RGB")
    labels_dir = _find_dataset_dir(root, "CamVid_Label")
    if not _dir_has_images(images_dir) or not _dir_has_images(labels_dir):
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
    failed_run = _finalize_dead_harness(manifest_path)
    encoded_manifest = html.escape(manifest)
    default_out = str(_harness_out_path(manifest_path))
    cache = _harness_cache_info(manifest_path)
    eval_role = _read_manifest_eval_role(manifest_path)
    frame_count = _jsonl_line_count(manifest_path)
    eta_text = _format_duration(_harness_eta_seconds(max(frame_count, 1), eval_role))
    is_test = _is_camvid_test_manifest(manifest_path)
    is_full = _is_camvid_full_manifest(manifest_path)
    _lane_flag = "".join(
        f" \\\n  {flag}" for flag in _optional_harness_model_flags(eval_role=eval_role)
    )
    role_config_flag = ""
    if eval_role == "vehicle":
        role_config_flag = " \\\n  --config docs/datasets/harness-config-drive.json"
    elif eval_role == "pedestrian":
        role_config_flag = " \\\n  --config docs/datasets/harness-config-walk.json"
    run_cmd = (
        "ios-vqa-app/perception-harness/.build/debug/PerceptionHarness \\\n"
        f"  --manifest {manifest} \\\n"
        f"  --out {default_out}{_lane_flag}{role_config_flag}"
    )
    role_banner = ""
    if eval_role == "vehicle":
        role_banner = (
            "<div class='status' style='border-color:#ffd60a'>"
            "<b>这份是机动车评估。</b>人行道不算可行驶区。"
            f"一键跑会用 TwinLiteNet 实验叠图（bundled Core ML，{frame_count} 帧，Intel Mac {eta_text}）。"
            "CamVid 线级真值已退役；看红/绿/蓝原色叠图请用 "
            "<a href='/diagnostics/twinlite/ui'>TwinLiteNet 画廊</a>。"
            "</div>"
        )
    elif eval_role == "pedestrian":
        role_banner = (
            "<div class='status'>"
            "<b>这份是行人评估。</b>真值绿线偏人行道，马路是慎行不是首选。"
            f"一键跑会用 TwinLiteNet 实验叠图（{frame_count} 帧，Intel Mac {eta_text}）。"
            "TwinLite 是 BDD 驾驶可走区，不能当行人可走真身；mc5 已退出日常 harness。"
            "</div>"
        )
    if is_full:
        if "drive" in manifest_path.name:
            test_name = "camvid-manifest-drive-test.jsonl"
        elif "walk" in manifest_path.name:
            test_name = "camvid-manifest-walk-test.jsonl"
        else:
            test_name = "camvid-manifest-test.jsonl"
        test_href = html.escape(f"/diagnostics/datasets/ios-harness/ui?manifest=docs/datasets/{test_name}")
        role_banner += (
            "<div class='status'>"
            f"这是全量 701 帧，改算法请先用 "
            f"<a href='{test_href}'>{html.escape(test_name)}</a>"
            "（四种街道场景各 3 张，大约 1 分钟）。全量留给发布前回归。"
            "</div>"
        )
    elif is_test:
        role_banner += (
            "<div class='status ok'>"
            "这是日常迭代 test 集：CamVid 四段行车各 3 张，不跑全量 701。"
            "</div>"
        )
    run_script = f"""<script>
async function runHarness(force) {{
  const status = document.getElementById('runStatus');
  const btn = document.getElementById('runBtn');
  const btn2 = document.getElementById('rerunBtn');
  status.style.display = 'block';
  status.className = 'status';
  status.textContent = force
    ? '正在启动真身感知…（{frame_count} 帧，Intel Mac {eta_text}，后台跑，页面自动刷新进度）'
    : '正在处理…（新鲜缓存会秒回；重跑在后台进行，页面自动刷新）';
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
    if (payload.status === 'started' || payload.status === 'already_running') {{
      status.className = 'status';
      status.innerHTML = '<pre style="margin:0;white-space:pre-wrap">' + (payload.reason || payload.note || '真身感知已在后台运行').replace(/</g,'&lt;') + '</pre>';
      setTimeout(function() {{ runHarness(false); }}, 5000);
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

    if cache.get("exists") and cache.get("count"):
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
  {role_banner}
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
  {role_banner}
  <div class='callout'>
    <p><b>推荐：一键在本机跑</b>（服务器直接调用 harness；二进制缺失或源码更新后会自动重新编译）</p>
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

    failed_banner = ""
    if failed_run and failed_run.get("status") == "error":
        err_reason = html.escape(str(failed_run.get("reason") or "真身感知运行失败"))
        err_stderr = html.escape(str(failed_run.get("stderr") or ""))
        failed_banner = (
            f"<div class='status error'>{err_reason}"
            + (f"<pre style='margin-top:8px;white-space:pre-wrap'>{err_stderr}</pre>" if err_stderr else "")
            + "<p class='muted'>上次任务已结束（不是仍在运行）。可再点一次「一键跑」重试。</p></div>"
        )

    if not predictions.strip():
        return _html_page("iPhone 真身评估", header + failed_banner + steps)

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

    # Region report — "how close is the walkable AREA the iPhone perceives to the
    # annotated truth", scored per-cell on the shared 64x48 grid. This is the axis
    # the user actually cares about (iPhone should perceive the green region), and
    # it upgrades the loop past 3-box status agreement.
    region_pairs, region_dropped = _region_pairs(manifest_rows, prediction_rows)
    region_report = evaluate_region_grids(region_pairs) if region_pairs else None

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

    if region_report is not None and region_report.get("scored"):
        r = region_report
        dropped_note = (
            f"，另有 {region_dropped} 帧缺网格未计入" if region_dropped else ""
        )
        region_cards = (
            "<div class='callout'><h2>可走区域指标（iPhone 感知 vs 真值 · 逐格）</h2>"
            "<p class='hint'>直接衡量「iPhone 感知出的绿色可走区域」离 CamVid 真值有多近："
            "逐帧把两张 64×48 可走网格逐格对比。这正是你要的目标——端上把可走区域看准。</p>"
            "<div class='grid'>"
            + "".join([
                card("区域 IoU", num(r.get("mean_iou")), "两块绿区整体重合度（越高越好）"),
                card("覆盖率 recall", num(r.get("mean_recall")), "真值可走被 iPhone 覆盖的比例（低=漏掉可走区）"),
                card("准确率 precision", num(r.get("mean_precision")), "iPhone 判为可走里真的可走的比例（低=把不可走当可走）"),
                card("区域虚报帧 false_go", r.get("region_false_go_frames"), "precision<0.5：大半「可走」判断是错的（安全红线）"),
                card("区域漏走帧 miss", r.get("region_miss_frames"), "recall<0.5：漏掉大半真实可走区"),
                card("参与帧 scored", r.get("scored"), f"共比对 {r.get('scored')} 帧{dropped_note}"),
            ])
            + "</div></div>"
        )
    else:
        region_cards = (
            "<div class='callout'><h2>可走区域指标</h2>"
            "<p class='muted'>manifest 缺少真值可走网格 traversable_grid，或预测缺少 traversable_grid，"
            "无法做区域评估。重生成带真值网格的 manifest 并重跑真身感知后即可显示。</p></div>"
        )

    cards = region_cards + "<h2 style='margin-top:1.5rem'>三区状态指标（已退役 · 仅供参考，不再门禁）</h2><div class='grid'>" + "".join([
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
        f"<p class='hint'>光看数字不够。逐帧视图会在 CamVid 原图上叠加 iPhone 真身识别出的"
        f"可通行区域（绿）、引导线与检测到的物体框，并和 CamVid 真值对比，让你直接看清“为什么漏报/误阻挡”。</p>"
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


def _harness_dir() -> Path:
    return _repo_root() / "ios-vqa-app" / "perception-harness"


def _iter_harness_source_files(harness_dir: Path) -> list[Path]:
    files = [harness_dir / "Package.swift"]
    sources = harness_dir / "Sources"
    if sources.is_dir():
        files.extend(path for path in sources.rglob("*") if path.is_file())
    return files


def _harness_binary_stale(harness_bin: Path, harness_dir: Path) -> bool:
    """True when the binary is missing or older than Package.swift / Sources (follows symlinks)."""
    if not harness_bin.is_file():
        return True
    try:
        bin_mtime = harness_bin.stat().st_mtime
    except OSError:
        return True
    for path in _iter_harness_source_files(harness_dir):
        try:
            if path.stat().st_mtime > bin_mtime:
                return True
        except OSError:
            continue
    return False


def _build_harness(harness_dir: Path, harness_bin: Path) -> dict | None:
    """Run ``swift build``. Return an error payload, or None on success."""
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
    return None


def _ensure_harness_binary(harness_dir: Path, harness_bin: Path) -> tuple[str, dict | None]:
    """Build when missing or sources are newer. Returns (note, error_payload)."""
    if not _harness_binary_stale(harness_bin, harness_dir):
        return "", None
    note = (
        "源码新于二进制，已重新编译 harness。"
        if harness_bin.is_file()
        else "已自动编译 harness。"
    )
    err = _build_harness(harness_dir, harness_bin)
    if err is not None:
        return "", err
    return note, None


def _harness_missing_images_reason(stderr_text: str) -> str:
    """Explain a predicted=0 run whose stderr is image_not_found, without blaming the wrong root."""
    if "docs/datasets/dataset/" in stderr_text:
        return (
            "运行结束但未产出预测：harness 把仓库相对路径 dataset/camvid/... "
            "接到了 manifest 所在目录 docs/datasets/ 下面"
            "（实际图片在仓库根 dataset/camvid/）。"
            "平台会在源码更新后自动重新编译 harness；请再点一次「一键在本机跑真身感知」。"
        )
    expected = open_dataset_root() / "camvid" / "CamVid_RGB"
    return (
        "运行结束但未产出预测：manifest 里引用的图片文件在磁盘上找不到。"
        f"当前数据集根是 {expected.parent.parent}，CamVid 应在 {expected}。"
        "若该目录为空，请回到「接入开源数据集」重新下载 CamVid，再重跑真身感知。"
    )


def _harness_models_dir() -> Path:
    """Where the compiled Core ML models the harness can inject live. Defaults to
    the durable cache (~/.cache/vqasee/models); overridable via VQASEE_MODELS_DIR."""
    configured = os.getenv("VQASEE_MODELS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "vqasee" / "models"


def _seg5_model_path() -> Path | None:
    """N=5 role-conditioned segmenter: bundled App copy first, then models cache."""
    bundled = (
        _repo_root()
        / "ios-vqa-app"
        / "VQASee"
        / "VQASee"
        / "VQASeeTraversabilitySeg5.mlmodelc"
    )
    cached = _harness_models_dir() / "VQASeeTraversabilitySeg5.mlmodelc"
    for path in (bundled, cached):
        if path.is_dir():
            return path
    return None


def _read_manifest_eval_role(manifest_path: Path) -> str | None:
    """Map a role-conditioned CamVid manifest onto the Swift wire role.

    ``walk`` / ``drive`` in jsonl are dataset roles; the harness config uses
    ``pedestrian`` / ``vehicle``. A manifest without ``role`` is not a
    role-conditioned eval (binary segmenter stays correct).
    """
    try:
        with manifest_path.open(encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                row = json.loads(text)
                raw = str(row.get("role") or "").strip().lower()
                if raw in ("drive", "vehicle"):
                    return "vehicle"
                if raw in ("walk", "pedestrian", "bike"):
                    return "pedestrian"
                return None
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _overlay_harness_config_for_manifest(payload: dict, eval_role: str | None) -> dict:
    """Align harness config with the dataset role without forcing mc5.

    Daily iteration uses bundled TwinLiteNet (same experimental App default).
    mc5 role IoU is opt-in manual only — too slow on Intel Mac and not the
    current product iteration surface.
    """
    if not eval_role:
        return payload
    merged = dict(payload)
    merged["role"] = eval_role
    merged["use_multiclass_segmentation"] = False
    merged["road_backend"] = "twinlite"
    return config_from_dict(merged).to_dict()


def _optional_harness_model_flags(*, eval_role: str | None = None) -> list[str]:
    """Append optional lane / role-segmentation models for the offline iPhone harness.

    Product lane lines are UFLDv2 ``lane_polylines`` when
    ``VQASeeLaneUFLDv2.mlmodelc`` exists. The older
    ``VQASeeLaneSegmentation.mlmodelc`` still emits ``lane_grid`` as a debug mask
    for regressions, but it is no longer the product lane-line surface.

    mc5 (``--seg-model``) is never injected here: daily harness uses TwinLiteNet
    via ``road_backend=twinlite``. Full mc5 role regression is manual only.
    """
    _ = eval_role  # reserved for future opt-in flags; mc5 is not auto-injected.
    flags: list[str] = []
    models_dir = _harness_models_dir()
    lane_mask = models_dir / "VQASeeLaneSegmentation.mlmodelc"
    if lane_mask.is_dir():
        flags += ["--lane-model", str(lane_mask)]
    lane_polyline = models_dir / "VQASeeLaneUFLDv2.mlmodelc"
    if lane_polyline.is_dir():
        flags += ["--lane-polyline-model", str(lane_polyline)]
    return flags


def _region_pairs(manifest_rows: list[dict], prediction_rows: list[dict]):
    """Build (frame_id, gt_grid, pred_grid) triples for region-IoU scoring.

    A frame participates only when BOTH the manifest GT walkable grid and the
    harness predicted grid are present; frames missing either are counted as
    dropped (surfaced), never silently scored as agreement."""
    preds: dict = {}
    for row in prediction_rows:
        fid = row.get("frame_id")
        if fid is not None and isinstance(row.get("traversable_grid"), dict):
            preds[str(fid)] = row["traversable_grid"]
    pairs = []
    dropped = 0
    for row in manifest_rows:
        fid = row.get("frame_id")
        gt_raw = row.get("traversable_grid")
        pred_raw = preds.get(str(fid)) if fid is not None else None
        if fid is None or not isinstance(gt_raw, dict) or pred_raw is None:
            dropped += 1
            continue
        pairs.append((str(fid), gt_raw, pred_raw))
    return pairs, dropped


def _harness_out_path(manifest_path: Path) -> Path:
    return Path(f"/tmp/{manifest_path.stem}-ios-harness.jsonl")


def _harness_meta_path(manifest_path: Path) -> Path:
    return Path(f"/tmp/{manifest_path.stem}-ios-harness.meta.json")


def _harness_lock_path(manifest_path: Path) -> Path:
    return Path(f"/tmp/{manifest_path.stem}-ios-harness.lock.json")


def _pid_is_running(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _active_harness_run(manifest_path: Path) -> dict | None:
    """Return active harness lock info, clearing stale locks first."""
    lock = _harness_lock_path(manifest_path)
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _active_harness_process(manifest_path)
    if str(info.get("manifest")) != str(manifest_path):
        return None
    if _pid_is_running(info.get("pid")):
        return info
    finished = _finalize_dead_harness(manifest_path)
    if finished is not None:
        return None
    return _active_harness_process(manifest_path)


def _active_harness_process(manifest_path: Path) -> dict | None:
    """Best-effort fallback for pre-lock harnesses already running on this Mac."""
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,etime=,command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    needle = f"--manifest {manifest_path}"
    for line in proc.stdout.splitlines():
        if "PerceptionHarness" not in line or needle not in line:
            continue
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        return {
            "pid": pid,
            "manifest": str(manifest_path),
            "started_at": f"已运行 {parts[1]}",
            "source": "process_scan",
        }
    return None


def _write_harness_lock(manifest_path: Path, *, pid: int, cmd: list[str], extra: dict | None = None) -> Path:
    lock = _harness_lock_path(manifest_path)
    payload = {
        "pid": pid,
        "manifest": str(manifest_path),
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cmd": cmd,
    }
    if extra:
        payload.update(extra)
    lock.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return lock


def _clear_harness_lock(manifest_path: Path, *, pid: int | None = None) -> None:
    lock = _harness_lock_path(manifest_path)
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    try:
        locked_pid = int(info.get("pid", -1))
    except (TypeError, ValueError):
        locked_pid = -1
    if pid is not None and locked_pid != pid:
        return
    try:
        lock.unlink()
    except OSError:
        pass


def _harness_stderr_path(manifest_path: Path) -> Path:
    return Path(f"/tmp/{manifest_path.stem}-ios-harness.stderr.log")


def _harness_exit_path(manifest_path: Path) -> Path:
    return Path(f"/tmp/{manifest_path.stem}-ios-harness.exit")


def _harness_runner_path(manifest_path: Path) -> Path:
    return Path(f"/tmp/{manifest_path.stem}-ios-harness.run.sh")


def _jsonl_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with path.open(encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _harness_seconds_per_frame(eval_role: str | None) -> float:
    # Measured on this Intel Mac: TwinLiteNet bundled Core ML ~1.0s/frame.
    # mc5 (~3.2s/frame) is manual-only and no longer the default harness path.
    _ = eval_role
    return 1.0


def _harness_eta_seconds(frame_count: int, eval_role: str | None) -> int:
    return max(1, int(frame_count * _harness_seconds_per_frame(eval_role)))


def _format_duration(seconds: int) -> str:
    minutes = max(1, (seconds + 59) // 60)
    if minutes < 60:
        return f"约 {minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"约 {hours} 小时 {minutes} 分钟" if minutes else f"约 {hours} 小时"


def _active_eval_config_payload(manifest_path: Path) -> dict:
    """Active perception config overlaid with the dataset role.

    Drive/walk manifests hash as vehicle/pedestrian + twinlite overlay, not as
    the stored pedestrian default — otherwise a finished role run looks stale.
    """
    payload = load_active_config().to_dict()
    role = _read_manifest_eval_role(manifest_path)
    return _overlay_harness_config_for_manifest(payload, role)


def _harness_progress_reason(manifest_path: Path, info: dict) -> str:
    expected = int(info.get("expected") or 0)
    predicted = _jsonl_line_count(_harness_out_path(manifest_path))
    eval_role = info.get("eval_role")
    remaining = max(0, expected - predicted) if expected else expected
    eta = _format_duration(_harness_eta_seconds(remaining or expected or 1, eval_role))
    started = info.get("started_at", "?")
    role_note = "TwinLiteNet 实验叠图" if eval_role else "YOLO + 路面模型"
    progress = f"{predicted}/{expected} 帧" if expected else f"已写出 {predicted} 帧"
    return (
        f"真身感知正在运行（pid {info.get('pid')}，开始于 {started}）。"
        f"{role_note}，进度 {progress}，剩余{eta}。"
        "请稍等，页面会自动刷新；不要再点一次以免误以为失败。"
    )


def _finalize_dead_harness(manifest_path: Path) -> dict | None:
    """If a lock exists but the process is gone, collect the result. Never unlink
    a live run. Survives diagnostics auto-reload because the child is detached."""
    lock = _harness_lock_path(manifest_path)
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if str(info.get("manifest")) != str(manifest_path):
        return None
    if _pid_is_running(info.get("pid")):
        return None
    exit_path = Path(info["exit_path"]) if info.get("exit_path") else _harness_exit_path(manifest_path)
    stderr_path = Path(info["stderr_path"]) if info.get("stderr_path") else _harness_stderr_path(manifest_path)
    stderr_text = ""
    if stderr_path.is_file():
        try:
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            stderr_text = ""
    stderr_tail = "\n".join(stderr_text.strip().splitlines()[-12:])
    exit_code: int | None = None
    if exit_path.is_file():
        try:
            exit_code = int(exit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
        except (OSError, ValueError):
            exit_code = None
    predicted = _jsonl_line_count(_harness_out_path(manifest_path))
    expected = int(info.get("expected") or 0)
    _clear_harness_lock(manifest_path, pid=info.get("pid"))
    incomplete = bool(expected) and predicted < expected
    killed = exit_code is None and incomplete
    failed = exit_code not in (None, 0) or predicted == 0 or killed
    if failed:
        reason = "真身感知运行失败。"
        if killed or exit_code == 143 or "SIGTERM" in stderr_text:
            reason = (
                "真身感知被中断（旧逻辑会在 15 分钟时杀掉仍在跑的长任务）。"
                "请再点一次重跑；现在会在后台跑完，不再用 15 分钟超时。"
            )
        elif predicted == 0:
            reason = "真身感知结束但未产出预测。"
            if "image_not_found:" in stderr_text:
                reason = _harness_missing_images_reason(stderr_text)
            elif "MLIR pass manager failed" in stderr_text or "MPSGraphExecutable" in stderr_text:
                reason = (
                    "Core ML 在 Mac GPU（MPSGraph）上编译/运行失败（进程异常退出）。"
                    "请确认已重新编译 harness（会自动改用 CPU-only）；若仍失败，把 stderr 贴给工程。"
                )
        return {
            "status": "error",
            "reason": reason,
            "returncode": exit_code,
            "stderr": stderr_tail,
            "predicted": predicted,
        }
    _write_harness_meta(
        manifest_path,
        count=predicted,
        config_version=info.get("config_version"),
        config_hash=info.get("config_hash"),
    )
    note = f"已在本机跑完真身感知，产出 {predicted} 帧预测"
    if expected and predicted < expected:
        note += f"（manifest {expected} 帧，有些图可能缺失）。"
    else:
        note += "。"
    return {
        "status": "ok",
        "predictions": str(_harness_out_path(manifest_path)),
        "predicted": predicted,
        "config_version": info.get("config_version"),
        "note": note,
        "stderr": stderr_tail,
    }


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
        eval_payload = _active_eval_config_payload(manifest_path)
        active_version = eval_payload.get("version")
        active_hash = eval_payload.get("hash")
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
                f"感知配置行为已变化（v{v_from}→v{active_version}，含角色/多类开关哈希不一致），需用新配置重跑"
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

    if count == 0:
        reasons.append("上次未产出任何预测，不能当缓存复用")

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
      - binary missing or sources newer -> best-effort `swift build`; if still missing, needs_build
      - harness non-zero rc  -> error with stderr tail (e.g. YOLO model not found)
    """
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="manifest_not_found")

    # Guard BEFORE anything else: a harness run needs a ground-truth dataset manifest
    # (rows carry image paths). Passing a predictions file (…-ios-harness.jsonl) is a
    # common footgun that otherwise yields a cryptic missing_image=N. Fail loud +
    # actionable (不允许静默失败).
    wrong_manifest = _manifest_runnable_reason(manifest_path)
    if wrong_manifest is not None:
        return {"status": "error", "capability": "wrong_manifest", "reason": wrong_manifest}

    finished = _finalize_dead_harness(manifest_path)
    if finished is not None and not force:
        return finished

    # Reuse cached predictions when nothing changed — cached results are
    # platform-independent to read, so allow this even off macOS.
    cache = _harness_cache_info(manifest_path)
    if not force and cache.get("exists") and cache.get("fresh") and cache.get("count"):
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

    active_run = _active_harness_run(manifest_path)
    if active_run is not None:
        return {
            "status": "already_running",
            "pid": active_run.get("pid"),
            "started_at": active_run.get("started_at"),
            "predicted": _jsonl_line_count(_harness_out_path(manifest_path)),
            "reason": _harness_progress_reason(manifest_path, active_run),
        }

    repo_root = _repo_root()
    harness_dir = _harness_dir()
    harness_bin = _harness_bin()

    build_note, build_err = _ensure_harness_binary(harness_dir, harness_bin)
    if build_err is not None:
        return build_err

    dataset_root = open_dataset_root()
    resolved_manifest = Path(f"/tmp/{manifest_path.stem}-harness-resolved.jsonl")
    try:
        resolve_stats = rewrite_manifest_resolved_paths(
            manifest_path,
            resolved_manifest,
            dataset_root=dataset_root,
            repo=repo_root,
        )
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "error",
            "reason": f"无法把 manifest 图片路径解析到磁盘文件：{exc}",
        }
    if resolve_stats["rows"] and resolve_stats["resolved"] == 0:
        example = resolve_stats["missing"][0] if resolve_stats["missing"] else ""
        expected = remap_stale_dataset_path(example, dataset_root=dataset_root)
        where = expected if expected is not None else (dataset_root / "camvid")
        return {
            "status": "error",
            "reason": (
                "manifest 里的图片路径在磁盘上解析不到文件"
                + (f"（例：{example}）" if example else "")
                + f"。期望位置：{where}。请确认仓库根 dataset/camvid 已下载。"
            ),
        }

    eval_role = _read_manifest_eval_role(manifest_path)

    out_path = str(_harness_out_path(manifest_path))
    cmd = [str(harness_bin), "--manifest", str(resolved_manifest), "--out", out_path]
    if limit and limit > 0:
        cmd += ["--limit", str(limit)]
    # Inject the lane model when available so the prediction carries lane_grid and
    # the per-frame view can draw lanes (otherwise lanes silently never appear).
    cmd += _optional_harness_model_flags(eval_role=eval_role)

    # Evaluate the CURRENTLY ACTIVE perception config so tuning it (and bumping
    # the version) is reflected in the harness result — this is what makes the
    # tune -> re-run -> gate -> ship loop coherent. Role-conditioned manifests
    # overlay pedestrian/vehicle + multiclass so drive GT is never scored against
    # the walker default. Fall back to compiled defaults (no --config) if the
    # active config can't be serialized; never fake success.
    config_file = None
    active_cfg: dict = {}
    try:
        active_cfg = _overlay_harness_config_for_manifest(
            load_active_config().to_dict(), eval_role
        )
        config_file = Path(f"/tmp/{manifest_path.stem}-perception-config.json")
        config_file.write_text(json.dumps(active_cfg), encoding="utf-8")
        cmd += ["--config", str(config_file)]
    except (ConfigValidationError, OSError):
        config_file = None
        active_cfg = {}

    expected = limit if (limit and limit > 0) else int(resolve_stats.get("resolved") or 0)
    eta_seconds = _harness_eta_seconds(max(expected, 1), eval_role)
    stderr_path = _harness_stderr_path(manifest_path)
    exit_path = _harness_exit_path(manifest_path)
    runner = _harness_runner_path(manifest_path)
    stdout_log = Path(f"/tmp/{manifest_path.stem}-ios-harness.stdout.log")
    try:
        exit_path.unlink()
    except OSError:
        pass
    quoted = " ".join(shlex.quote(part) for part in cmd)
    runner.write_text(
        "#!/bin/bash\n"
        "set +e\n"
        f"{quoted} >{shlex.quote(str(stdout_log))} 2>{shlex.quote(str(stderr_path))}\n"
        f"echo $? > {shlex.quote(str(exit_path))}\n",
        encoding="utf-8",
    )
    os.chmod(runner, 0o755)
    proc = subprocess.Popen(
        ["/bin/bash", str(runner)],
        cwd=str(repo_root),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    lock_extra = {
        "expected": expected,
        "eval_role": eval_role,
        "config_version": active_cfg.get("version"),
        "config_hash": active_cfg.get("hash"),
        "stderr_path": str(stderr_path),
        "exit_path": str(exit_path),
        "eta_seconds": eta_seconds,
    }
    _write_harness_lock(manifest_path, pid=proc.pid, cmd=cmd, extra=lock_extra)
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "status": "started",
        "pid": proc.pid,
        "predictions": out_path,
        "expected": expected,
        "eta_seconds": eta_seconds,
        "note": (
            build_note
            + f"已在后台启动真身感知（{expected} 帧，{_format_duration(eta_seconds)}）。"
            "页面会自动刷新进度；完成后打开评估。不会再因 15 分钟超时杀掉任务。"
        ).strip(),
        "reason": _harness_progress_reason(
            manifest_path,
            {"pid": proc.pid, "started_at": started_at, **lock_extra},
        ),
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


def _lane_polylines_svg(polylines: object) -> str:
    """Render product lane-line geometry from UFLDv2-style normalized polylines.

    Lane points are image-normalized with a TOP-left origin, unlike guidance-path
    Vision coordinates. Invalid/malformed lanes are skipped so the page does not
    draw garbage or fabricate a lane line.
    """
    if not isinstance(polylines, list):
        return ""
    colors = ["#ffd60a", "#ff9f0a", "#64d2ff", "#bf5af2", "#30d158"]
    parts: list[str] = []
    for lane_index, lane in enumerate(polylines[:8]):
        if not isinstance(lane, dict):
            continue
        points = lane.get("points")
        if not isinstance(points, list) or len(points) < 2:
            continue
        coords: list[str] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            try:
                x = min(max(float(point.get("x", 0.0)), 0.0), 1.0) * 100.0
                y = min(max(float(point.get("y", 0.0)), 0.0), 1.0) * 100.0
            except (TypeError, ValueError):
                continue
            coords.append(f"{x:.2f},{y:.2f}")
        if len(coords) < 2:
            continue
        color = colors[lane_index % len(colors)]
        pts = " ".join(coords)
        parts.append(
            f"<polyline points='{pts}' fill='none' stroke='#000' stroke-opacity='0.45' "
            "stroke-width='2.2' stroke-linejoin='round' stroke-linecap='round'/>"
        )
        parts.append(
            f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='1.25' "
            "stroke-linejoin='round' stroke-linecap='round'/>"
        )
    if not parts:
        return ""
    return (
        "<svg class='lane-polylines' viewBox='0 0 100 100' preserveAspectRatio='none' "
        "xmlns='http://www.w3.org/2000/svg' aria-label='iPhone 车道折线'>"
        + "".join(parts)
        + "</svg>"
    )


def _guidance_status_ok(path: dict | None) -> bool:
    """A guidance line "exists" (walkable path found) when it is a dict with an
    explicit ``status == 'ok'``. Any other value (``insufficient`` / missing /
    malformed) reads as "no line" — surfaced, never guessed."""
    return isinstance(path, dict) and str(path.get("status")) == "ok"


def _overlay_svg(
    objects: list,
    guidance_path: dict | None = None,
) -> str:
    """Build an SVG overlay (viewBox 0..100, stretched to the image) drawing the
    detected object boxes and (when present) the predicted guidance line.
    CamVid line-level ground truth was retired — only iPhone prediction is drawn.
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

    if guidance_path:
        parts.append(_guidance_line_svg(guidance_path, color="#bf5af2", dashed=False, corridor=True, label="预测", width=2.4))

    parts.append("</svg>")
    return "".join(parts)


_FRAME_FILTERS = {
    "region_miss": ("漏报可走", "真值可走格子，预测不可走"),
    "region_false_go": ("误判可走", "真值不可走格子，预测可走"),
    "mismatch": ("有分歧", "区域网格预测≠真实"),
    "correct": ("全对", "区域网格与真值一致"),
    "no_prediction": ("无预测", "该帧没有对应预测行"),
}


def _frame_flags(gt: dict, prediction: dict) -> set:
    if not prediction:
        return {"no_prediction"}
    flags = set(frame_failure_types(gt, prediction))
    gt_cells = _grid_cells(gt.get("traversable_grid"))
    pred_cells = _grid_cells(prediction.get("traversable_grid"))
    if gt_cells is None or pred_cells is None or len(gt_cells) != len(pred_cells):
        return flags
    if gt_cells == pred_cells:
        flags.add("correct")
    elif "region_miss" not in flags and "region_false_go" not in flags:
        flags.add("mismatch")
    else:
        flags.add("mismatch")
    return flags


@router.get("/datasets/ios-harness/frames/ui", response_class=HTMLResponse)
def dataset_ios_harness_frames_ui(
    manifest: str, predictions: str, page: int = 1, filter: str = "all"
):
    """Per-frame visualization: draw the iPhone on-device perception output
    (detected object boxes + perceived traversable region + guidance line +
    lane markings) on top of each CamVid image, side by side with the
    ground-truth answer. This is the "看得见" view that turns aggregate metrics
    into inspectable pictures."""
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

    eval_role = _read_manifest_eval_role(manifest_path)
    if eval_role == "vehicle":
        overlay_legend = (
            "<b style='color:#bf5af2'>紫实线=iPhone 预测路径</b> · "
            "蓝虚框=检测物体；<b style='color:#30d158'>绿色叠层=车可通行区（不含人行道）</b>；"
            "<b style='color:#ffd60a'>黄色实线=iPhone 车道折线</b> · "
            "<b style='color:#ffd60a'>黄块=像素车道调试层</b>。"
            " CamVid 线/车道真值已退役；红绿蓝 TwinLite 原色见 "
            "<a href='/diagnostics/twinlite/ui'>TwinLite 画廊</a>。"
        )
    else:
        overlay_legend = (
            "<b style='color:#bf5af2'>紫实线=iPhone 预测路径</b> · "
            "蓝虚框=检测物体；<b style='color:#30d158'>绿色叠层=可走区域</b>；"
            "<b style='color:#ffd60a'>黄色实线=iPhone 车道折线</b> · "
            "<b style='color:#ffd60a'>黄块=像素车道调试层</b>。"
        )

    # Classify every frame once so we can both filter and show per-category counts
    # (with 701 frames the user needs to jump straight to the bad ones).
    flags_by_index: list = []
    counts = {key: 0 for key in _FRAME_FILTERS}
    for row in manifest_rows:
        gt, pred = guidance_payloads(row, pred_index.get(str(row.get("frame_id", ""))) or {})
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

    cards = []
    for row in page_rows:
        frame_id = str(row.get("frame_id", ""))
        image_path = str(row.get("image_path") or "")
        gt = row.get("ground_truth", {}) or {}
        pred_row = pred_index.get(frame_id, {})
        prediction = pred_row.get("prediction", {}) or {}
        objects = pred_row.get("objects", []) or []
        pred_guidance = pred_row.get("guidance_path") if isinstance(pred_row.get("guidance_path"), dict) else None

        # Optional CamVid traversable-region tint (toggle in page chrome).
        label_path = str(row.get("label_path") or "")
        role = str(row.get("role") or "").strip().lower()
        if role == "drive":
            tclasses = "drive"
        elif role == "walk":
            tclasses = "walk"
        else:
            tclasses = str(row.get("traversable_classes") or "walk")
        mask_layer = ""
        if label_path:
            mask_src = (
                f"/diagnostics/camvid-mask?label={html.escape(label_path)}"
                f"&classes={html.escape(tclasses)}&w=560"
            )
            mask_layer = (
                f"<img class='gt-mask' loading='lazy' decoding='async' "
                f"src='{mask_src}' alt='CamVid 真值可走区域'>"
            )

        # iPhone-perceived walkable region: the segmentation model's own traversable
        # raster (same signal behind the 3-region status + guidance line), surfaced
        # whole so it can be compared pixel-for-pixel with the GT green region.
        pred_mask_layer = ""
        grid_uri = _traversable_grid_png_datauri(pred_row.get("traversable_grid"))
        if grid_uri:
            pred_mask_layer = (
                f"<img class='pred-mask' decoding='async' "
                f"src='{grid_uri}' alt='iPhone 感知可走区域'>"
            )

        # Lane-marking layers. Product lane output is geometry-first: UFLDv2-style
        # `lane_polylines` render as true lines. The old pixel `lane_grid` remains
        # visible only as a debug mask so blocky over-spray is not mistaken for the
        # lane-line product target.
        lane_layer = ""
        pred_lane_debug_uri = _lane_grid_png_datauri(pred_row.get("lane_grid"), (255, 214, 10, 120))
        if pred_lane_debug_uri:
            lane_layer += (
                f"<img class='pred-mask' decoding='async' "
                f"src='{pred_lane_debug_uri}' alt='旧像素车道调试层'>"
            )
        lane_polyline_svg = _lane_polylines_svg(pred_row.get("lane_polylines"))
        if lane_polyline_svg:
            lane_layer += lane_polyline_svg

        if not image_path:
            image_block = "<p class='muted'>无图片路径</p>"
        elif not prediction:
            thumb = f"/diagnostics/local-file?path={html.escape(image_path)}&w=560"
            image_block = (
                f"<div class='frame-overlay'><img loading='lazy' decoding='async' "
                f"src='{thumb}' alt='{html.escape(frame_id)}'>{mask_layer}{pred_mask_layer}{lane_layer}</div>"
                f"<p class='muted'>该帧没有对应预测（可能被 --limit 截断）。</p>"
            )
        else:
            thumb = f"/diagnostics/local-file?path={html.escape(image_path)}&w=560"
            full = f"/diagnostics/local-file?path={html.escape(image_path)}"
            overlay = _overlay_svg(objects, pred_guidance)
            image_block = (
                f"<a href='{full}' target='_blank'><div class='frame-overlay'>"
                f"<img loading='lazy' decoding='async' src='{thumb}' alt='{html.escape(frame_id)}'>"
                f"{mask_layer}{pred_mask_layer}{lane_layer}{overlay}</div></a>"
            )

        pred_line_ok = _guidance_status_ok(pred_guidance)
        if prediction:
            path_note = (
                "<span style='color:#30d158'>已画出预测路径</span>"
                if pred_line_ok
                else "<span class='muted'>本帧未画出预测路径（insufficient）</span>"
            )
        else:
            path_note = "<span class='muted'>该帧无预测</span>"

        obj_labels = ", ".join(
            html.escape(str(o.get("label") or o.get("kind") or "物体")) for o in objects
        ) or "（未检出物体）"

        cards.append(
            f"""<div class='card'><h2>{html.escape(frame_id)}</h2>
<div class='row'>
  <div>{image_block}
    <p class='explain'>{overlay_legend}</p>
  </div>
  <div>
    <p class='explain'><b>预测路径：</b>{path_note}</p>
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
        f"<p class='hint' style='margin-top:6px'><b>主信号 · 可走区域</b>："
        f"<span style='color:#30d158'>绿色叠层</span>=iPhone 感知可走区（TwinLite DA 等）；"
        f"与 CamVid 真值区域（可开关）逐格比 IoU。"
        f" CamVid 线级/车道真值已退役，不再画绿虚线或蓝车道层。</p>"
        f"<p class='hint' style='margin-top:6px'><b>辅助</b>："
        f"<span style='color:#bf5af2'>紫实线</span>=iPhone 预测路径（有则显示）；"
        f"<span style='color:#64d2ff'>蓝虚框</span>=YOLO 检测物体。"
        f" TwinLite 红/绿/蓝原色见 <a href='/diagnostics/twinlite/ui'>TwinLite 画廊</a>。</p></div>"
    )

    has_gt_mask = any(row.get("label_path") for row in manifest_rows)
    has_pred_mask = any(
        _traversable_grid_to_mask(r.get("traversable_grid")) is not None
        for r in pred_index.values()
    )
    # Both regions are green. To compare them cleanly we show ONE at a time by
    # default: the iPhone-perceived region is on (that's the thing under review),
    # the CamVid truth is one toggle away. Turn both on to see overlap/gap.
    toggles = []
    if has_pred_mask:
        toggles.append(
            "<label style='cursor:pointer;user-select:none;display:block;margin:2px 0'>"
            "<input type='checkbox' id='predMaskToggle' checked "
            "onchange=\"document.getElementById('framesWrap')"
            ".classList.toggle('hide-pred-mask', !this.checked)\"> "
            "<b style='color:#30d158'>显示 iPhone 感知的可走区域</b>"
            "（绿色半透明方块 = 端上分割模型判定可走的区域，粗网格是它真实的分辨率）"
            "</label>"
        )
    if has_gt_mask:
        toggles.append(
            "<label style='cursor:pointer;user-select:none;display:block;margin:2px 0'>"
            "<input type='checkbox' id='gtMaskToggle' "
            "onchange=\"document.getElementById('framesWrap')"
            ".classList.toggle('hide-gt-mask', !this.checked)\"> "
            "<b style='color:#30d158'>显示 CamVid 真值可走区域</b>"
            "（绿色半透明 = 标注推出的答案；和上面对比就能看出 iPhone 感知差多少）"
            "</label>"
        )
    mask_toggle = ""
    if toggles:
        mask_toggle = (
            "<div class='card' style='padding:10px 12px'>"
            "<p class='hint' style='margin:0 0 6px'>两块都是绿色：默认只显示 iPhone 感知，"
            "打开真值即可叠着看差距（都是绿色，建议一次开一个更清楚）。</p>"
            + "".join(toggles)
            + "</div>"
        )
    # Default view: iPhone region ON (hide-gt-mask hides the truth layer initially).
    wrap_classes = "hide-gt-mask" if has_gt_mask else ""
    cards_html = (
        f"<div id='framesWrap' class='{wrap_classes}'>{''.join(cards) or empty_msg}</div>"
    )
    body = header + filter_bar + mask_toggle + nav + cards_html + (nav if cards else "")
    return _html_page("逐帧识别效果", body)


@router.post("/cases/cluster")
def cases_cluster(manifest: str, predictions: str) -> dict:
    """Cluster this eval run's failing frames into cases (create or update).

    This is the AutoTriage-lite entry: read the manifest + harness predictions,
    bucket region_miss / region_false_go frames, and upsert a case per bucket with a
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
    """Apply partial threshold / backend updates, bump the version, persist.

    Rejects invalid values and retired three-zone ``roi`` writes with a 400
    and writes nothing, so a bad edit can never be shipped to devices.
    """
    try:
        new_config = bump_and_save(updates or {})
    except ConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=f"invalid_config: {exc}") from exc
    return {"status": "ok", "config": new_config.to_dict(), "store": str(config_store_path())}


@router.get("/perception-config/ui", response_class=HTMLResponse)
def perception_config_ui():
    config = load_active_config().to_dict()
    thr = config["thresholds"]

    def num(name: str, value: float, label: str, hint: str = "") -> str:
        return (
            f"<label class='num'>{html.escape(label)}"
            f"<input type='number' step='0.01' min='0' max='1' id='{name}' value='{value}'>"
            f"<span class='muted'>{html.escape(hint)}</span></label>"
        )

    thr_block = (
        "<div class='card'><h2>阈值</h2><div class='row'>"
        + num("seg_traversable_pixel", thr["seg_traversable_pixel"], "分割可走像素阈值")
        + "</div></div>"
    )
    backend = str(config.get("road_backend") or "twinlite")
    options = "".join(
        f"<option value='{html.escape(item)}'{' selected' if item == backend else ''}>{html.escape(item)}</option>"
        for item in ("off", "twinlite", "mc5")
    )
    backend_block = (
        "<div class='card'><h2>路面模型</h2>"
        "<p class='muted'>实验切换：twinlite=驾驶可走区+车道线叠图；mc5=CamVid 人/车可走区；off=不跑。"
        "换模型不改 App 架构，只换这一项。下次打开 App 才加载新模型。</p>"
        f"<label class='num'>road_backend<select id='road_backend'>{options}</select></label>"
        "</div>"
    )

    script = """<script>
function val(id){return parseFloat(document.getElementById(id).value);}
async function saveConfig(){
  const status = document.getElementById('cfgStatus');
  status.style.display='block'; status.className='status'; status.textContent='正在校验并升级版本…';
  const updates = {
    thresholds:{
      seg_traversable_pixel:val('seg_traversable_pixel')
    },
    road_backend: document.getElementById('road_backend').value
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
        "<p><a href='/diagnostics/ui'>← 感知能力总览</a></p>"
        f"<h1>感知配置（当前 v{config['version']}）</h1>"
        "<p class='hint'>三区 ROI 已退役，不再下发近/左/右框。这里只调分割阈值和路面模型。"
        "先在 iPhone 真身评估里验证候选参数，再回到这里保存并升级版本，iPhone 下次连接自动生效。</p>"
        + script
        + thr_block
        + backend_block
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
