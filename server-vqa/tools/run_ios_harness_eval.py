#!/usr/bin/env python3
"""Evaluate the iPhone on-device perception stack against a platform manifest.

Feeds the macOS offline harness output (predictions produced by the REAL iPhone
perception stack: YOLO11n Core ML + LocalPathGuidanceEngine) into the closed-loop
platform:

1. `evaluate_path_guidance(manifest, harness_predictions)` -> accuracy / risk_miss
   / false_block against the manifest's objective ground truth.
2. Optional parity vs the server offline ONNX proxy (`compute_parity`), to see
   where the two independent predictors diverge.

Build + run the harness first (macOS, requires the app's Core ML models):

    (cd ios-vqa-app/perception-harness && swift build)
    ios-vqa-app/perception-harness/.build/debug/PerceptionHarness \
        --manifest docs/datasets/camvid-manifest.jsonl \
        --out docs/datasets/camvid-ios-harness.jsonl

    python server-vqa/tools/run_ios_harness_eval.py \
        --manifest docs/datasets/camvid-manifest.jsonl \
        --predictions docs/datasets/camvid-ios-harness.jsonl \
        --parity

Honesty: the harness reflects the iPhone "camera-only" branch (no LiDAR depth on
macOS). Parity requires onnxruntime + a segmentation ONNX; if unavailable this
reports the reason instead of pretending.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.eval_baseline import load_baseline, save_baseline  # noqa: E402
from app.guidance_path import GuidancePath, GuidancePathError  # noqa: E402
from app.guidance_path_eval import (  # noqa: E402
    GUIDANCE_BASELINE_KEYS,
    evaluate_guidance_paths,
    gate_guidance,
)
from app.path_dataset_eval import evaluate_path_guidance, load_jsonl  # noqa: E402
from app.path_parity import compute_parity  # noqa: E402
from app.region_grid import (  # noqa: E402
    REGION_BASELINE_KEYS,
    evaluate_region_grids,
    gate_region,
)
from app.lane_metrics import (  # noqa: E402
    LANE_BASELINE_KEYS,
    evaluate_lane,
    gate_lane,
)
from app.obstacle_metrics import (  # noqa: E402
    OBSTACLE_BASELINE_KEYS,
    evaluate_obstacles,
    gate_obstacle,
)
from app.region_grid import grid_from_wire  # noqa: E402
from app.role_metrics import (  # noqa: E402
    ROLE_BASELINE_KEYS,
    evaluate_role_region,
    gate_role_region,
)
from app.traversability_predictor import TraversabilityPredictor, predict_manifest  # noqa: E402


def _guidance_pairs(manifest_rows: list[dict], prediction_rows: list[dict]):
    """Build (frame_id, gt_path, pred_path) triples where BOTH sides carry a
    guidance line. Malformed entries are surfaced as skips, never silently
    coerced into a fake straight line."""
    preds: dict[str, dict] = {}
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
            gt = GuidancePath.from_dict(gt_raw)
            pred = GuidancePath.from_dict(pred_raw)
        except GuidancePathError:
            skipped += 1
            continue
        pairs.append((fid, gt, pred))
    return pairs, skipped


def _role_pairs(role_rows: list[dict], prediction_rows: list[dict]):
    """Build (frame_id, role_grids_wire, pred_grid_wire) triples where BOTH sides
    are present. A role manifest carries per-frame ``role_grids`` (primary/caution/
    lane/obstacle); predictions carry the device walkable ``traversable_grid``.
    Frames missing either are dropped (surfaced), never scored as silent agreement."""
    preds: dict[str, dict] = {}
    for row in prediction_rows:
        fid = row.get("frame_id")
        if fid is not None and isinstance(row.get("traversable_grid"), dict):
            preds[fid] = row["traversable_grid"]
    pairs = []
    dropped = 0
    for row in role_rows:
        fid = row.get("frame_id")
        role_grids = row.get("role_grids")
        pred_raw = preds.get(fid)
        if fid is None or not isinstance(role_grids, dict) or pred_raw is None:
            dropped += 1
            continue
        pairs.append((fid, role_grids, pred_raw))
    return pairs, dropped


def _lane_pairs(role_rows: list[dict], prediction_rows: list[dict]):
    """Build (frame_id, pred_lane_grid, gt_lane_grid) numpy triples for closed-loop
    lane scoring. GT is the fine ``lane_grid_fine`` (128x96 any-pixel) from the role
    manifest; prediction is the on-device ``lane_grid``. Only frames where BOTH exist
    AND shapes match are scored; the rest are surfaced as dropped (never silent)."""
    preds: dict[str, Any] = {}
    for row in prediction_rows:
        fid = row.get("frame_id")
        if fid is not None and isinstance(row.get("lane_grid"), dict):
            preds[fid] = row["lane_grid"]
    pairs = []
    dropped = 0
    for row in role_rows:
        fid = row.get("frame_id")
        gt = grid_from_wire(row.get("lane_grid_fine"))
        pred = grid_from_wire(preds.get(fid))
        if fid is None or gt is None or pred is None or gt.shape != pred.shape:
            dropped += 1
            continue
        pairs.append((fid, pred, gt))
    return pairs, dropped


def _obstacle_pairs(role_rows: list[dict], prediction_rows: list[dict]):
    """Build (frame_id, gt_obstacle_grid_wire, objects) triples. GT obstacle cells
    come from the role manifest's ``role_grids.obstacle``; predicted obstacle boxes
    come from the harness ``objects`` list. Frames missing the GT obstacle layer are
    dropped (surfaced), never scored as silent agreement."""
    preds: dict[str, list] = {}
    for row in prediction_rows:
        fid = row.get("frame_id")
        if fid is not None and isinstance(row.get("objects"), list):
            preds[fid] = row["objects"]
    pairs = []
    dropped = 0
    for row in role_rows:
        fid = row.get("frame_id")
        role_grids = row.get("role_grids")
        if fid is None or not isinstance(role_grids, dict) or not isinstance(role_grids.get("obstacle"), dict):
            dropped += 1
            continue
        pairs.append((fid, role_grids["obstacle"], preds.get(fid, [])))
    return pairs, dropped


def _region_pairs(manifest_rows: list[dict], prediction_rows: list[dict]):
    """Build (frame_id, gt_grid, pred_grid) triples where BOTH sides carry a
    walkable-region grid. Frames missing either grid are dropped (surfaced), never
    scored as silent agreement."""
    preds: dict[str, dict] = {}
    for row in prediction_rows:
        fid = row.get("frame_id")
        if fid is not None and isinstance(row.get("traversable_grid"), dict):
            preds[fid] = row["traversable_grid"]
    pairs = []
    dropped = 0
    for row in manifest_rows:
        fid = row.get("frame_id")
        gt_raw = row.get("traversable_grid")
        pred_raw = preds.get(fid)
        if fid is None or not isinstance(gt_raw, dict) or pred_raw is None:
            dropped += 1
            continue
        pairs.append((fid, gt_raw, pred_raw))
    return pairs, dropped


EXIT_OK = 0
EXIT_NO_PREDICTIONS = 3
EXIT_REGRESSED = 4


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate iPhone on-device perception (harness output) against a manifest."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="GT manifest JSONL (has ground_truth per frame).")
    parser.add_argument("--predictions", type=Path, required=True, help="Harness prediction JSONL (frame_id + prediction).")
    parser.add_argument("--role-manifest", type=Path, help="Role-conditioned manifest JSONL (has role_grids per frame) for walk/drive metrics.")
    parser.add_argument("--parity", action="store_true", help="Also compare vs the server ONNX proxy predictor.")
    parser.add_argument("--parity-model", type=Path, help="Path to traversability segmentation ONNX (for parity).")
    parser.add_argument("--parity-threshold", type=float, default=0.20, help="Drift alert threshold for parity.")
    parser.add_argument("--baseline", type=str, help="If set, save the eval as a named regression baseline.")
    parser.add_argument("--gate", type=str, help="Baseline name to gate against; exits non-zero if quality regressed.")
    parser.add_argument("--out", type=Path, help="Write the full report JSON here (default: stdout only).")
    args = parser.parse_args()

    manifest_rows = load_jsonl(args.manifest)
    prediction_rows = load_jsonl(args.predictions)
    if not prediction_rows:
        sys.stderr.write(
            "no harness predictions found; build+run the Swift harness first "
            "(ios-vqa-app/perception-harness).\n"
        )
        return EXIT_NO_PREDICTIONS

    report = evaluate_path_guidance(manifest_rows, prediction_rows)
    report["prediction_source"] = "ios_coreml_offline_harness"

    output: dict = {
        "manifest": str(args.manifest),
        "predictions": str(args.predictions),
        "evaluation": report,
    }

    # Line-level guidance evaluation (predicted vs GT traversable line).
    guidance_pairs, guidance_skipped = _guidance_pairs(manifest_rows, prediction_rows)
    guidance_report = None
    if guidance_pairs:
        guidance_report = evaluate_guidance_paths(guidance_pairs)
        guidance_report["skipped_malformed"] = guidance_skipped
        # Keep the top-level output compact; per-frame detail stays available but
        # is not needed for gating.
        compact = {k: v for k, v in guidance_report.items() if k != "per_frame"}
        output["guidance_line"] = compact
    else:
        output["guidance_line"] = {
            "status": "unavailable",
            "reason": "manifest lacks ground_truth_path or predictions lack guidance_path.",
        }

    # Region-level evaluation: how close is the walkable AREA the iPhone perceives
    # to the annotated truth (per-cell IoU / recall / precision on the shared grid).
    region_pairs, region_dropped = _region_pairs(manifest_rows, prediction_rows)
    region_report = None
    if region_pairs:
        region_report = evaluate_region_grids(region_pairs)
        region_report["dropped_missing_grid"] = region_dropped
        output["region"] = region_report
    else:
        output["region"] = {
            "status": "unavailable",
            "reason": "manifest lacks traversable_grid (GT) or predictions lack traversable_grid.",
        }

    # Role-conditioned evaluation: how often the device walkable prediction routes a
    # walker onto the road (Road-as-Sidewalk), misses the sidewalk, or overlaps an
    # obstacle. Needs a role manifest (carries role_grids); otherwise stays silent.
    role_report = None
    obstacle_report = None
    lane_report = None
    if args.role_manifest is not None:
        role_rows = load_jsonl(args.role_manifest)
        role_pairs, role_dropped = _role_pairs(role_rows, prediction_rows)
        if role_pairs:
            role_report = evaluate_role_region(role_pairs)
            role_report["dropped_missing_grid"] = role_dropped
            output["role"] = role_report
        else:
            output["role"] = {
                "status": "unavailable",
                "reason": "role manifest lacks role_grids or predictions lack traversable_grid.",
            }

        # Closed-loop lane scoring: predicted lane_grid vs fine GT lane grid. Only
        # runs when the harness emitted lane_grid (i.e. a lane model was loaded) AND
        # the manifest carries lane_grid_fine.
        lane_pairs, lane_dropped = _lane_pairs(role_rows, prediction_rows)
        if lane_pairs:
            lane_report = evaluate_lane(lane_pairs, tol=2)
            lane_report["dropped_missing_grid"] = lane_dropped
            output["lane_loop"] = lane_report
        else:
            lane_report = None
            output["lane_loop"] = {
                "status": "unavailable",
                "reason": "predictions lack lane_grid (no --lane-model) or manifest lacks lane_grid_fine.",
            }

        # Obstacle coverage: do the YOLO boxes land on CamVid obstacle pixels? Uses
        # the same role manifest (obstacle layer) + prediction objects.
        obstacle_pairs, obstacle_dropped = _obstacle_pairs(role_rows, prediction_rows)
        if obstacle_pairs:
            obstacle_report = evaluate_obstacles(obstacle_pairs)
            obstacle_report["dropped_missing_grid"] = obstacle_dropped
            output["obstacle"] = obstacle_report
        else:
            output["obstacle"] = {
                "status": "unavailable",
                "reason": "role manifest lacks role_grids.obstacle.",
            }

    if args.parity:
        predictor = TraversabilityPredictor(model_path=args.parity_model)
        server_result = predict_manifest(manifest_rows, predictor)
        capability = server_result["capability"]
        if capability.get("capability") != "active":
            output["parity"] = {
                "status": "unavailable",
                "reason": capability.get("reason", ""),
                "note": "server ONNX proxy unavailable; parity skipped (explicit, not silent).",
            }
        else:
            parity = compute_parity(
                prediction_rows,
                server_result["predictions"],
                drift_threshold=args.parity_threshold,
            )
            output["parity"] = {"status": "ok", **parity}

    if args.baseline:
        # The legacy ROI-status baseline (near/left/right three-box agreement) is
        # RETIRED as a gated signal — the guidance LINE + walkable REGION grid are
        # what the platform draws and scores now. We only persist those two.
        output["baseline_note"] = (
            "roi-status baseline retired; gating on guidance-line + region grids."
        )
        if guidance_report is not None:
            g_saved = save_baseline(
                f"{args.baseline}-guidance",
                {k: v for k, v in guidance_report.items() if k != "per_frame"},
                source="ios_coreml_offline_harness_guidance",
                metric_keys=GUIDANCE_BASELINE_KEYS,
            )
            output["guidance_baseline"] = str(g_saved)
        if region_report is not None:
            r_saved = save_baseline(
                f"{args.baseline}-region",
                region_report,
                source="ios_coreml_offline_harness_region",
                metric_keys=REGION_BASELINE_KEYS,
            )
            output["region_baseline"] = str(r_saved)
        if role_report is not None:
            role_saved = save_baseline(
                f"{args.baseline}-role",
                role_report,
                source="ios_coreml_offline_harness_role",
                metric_keys=ROLE_BASELINE_KEYS,
            )
            output["role_baseline"] = str(role_saved)
        if obstacle_report is not None:
            obs_saved = save_baseline(
                f"{args.baseline}-obstacle",
                obstacle_report,
                source="ios_coreml_offline_harness_obstacle",
                metric_keys=OBSTACLE_BASELINE_KEYS,
            )
            output["obstacle_baseline"] = str(obs_saved)
        if lane_report is not None:
            # Closed-loop lane baseline kept under a DISTINCT name so it never
            # collides with the held-out generalization baseline (camvid-ios-lane)
            # the scorecard reads — they measure different things.
            lane_saved = save_baseline(
                f"{args.baseline}-lane-loop",
                lane_report,
                source="ios_coreml_offline_harness_lane_loop",
                metric_keys=LANE_BASELINE_KEYS,
            )
            output["lane_loop_baseline"] = str(lane_saved)

    gate_regressed = False
    if args.gate:
        gated_any = False
        # Guidance-line gate (predicted vs GT traversable line).
        g_baseline = load_baseline(f"{args.gate}-guidance")
        if g_baseline is not None and guidance_report is not None:
            passed, reasons = gate_guidance(guidance_report, g_baseline)
            output["guidance_gate"] = {"passed": passed, "reasons": reasons}
            gate_regressed = gate_regressed or not passed
            gated_any = True

        # Walkable-region gate (per-cell IoU / recall / precision on the grid).
        r_baseline = load_baseline(f"{args.gate}-region")
        if r_baseline is not None and region_report is not None:
            passed, reasons = gate_region(region_report, r_baseline)
            output["region_gate"] = {"passed": passed, "reasons": reasons}
            gate_regressed = gate_regressed or not passed
            gated_any = True

        # Role-safety gate (walker onto road / into obstacle must not increase).
        role_baseline = load_baseline(f"{args.gate}-role")
        if role_baseline is not None and role_report is not None:
            passed, reasons = gate_role_region(role_report, role_baseline)
            output["role_gate"] = {"passed": passed, "reasons": reasons}
            gate_regressed = gate_regressed or not passed
            gated_any = True

        # Obstacle gate (seeing FEWER obstacles is a safety regression).
        obstacle_baseline = load_baseline(f"{args.gate}-obstacle")
        if obstacle_baseline is not None and obstacle_report is not None:
            passed, reasons = gate_obstacle(obstacle_report, obstacle_baseline)
            output["obstacle_gate"] = {"passed": passed, "reasons": reasons}
            gate_regressed = gate_regressed or not passed
            gated_any = True

        # Closed-loop lane gate (lane recall / IoU must not regress).
        lane_loop_baseline = load_baseline(f"{args.gate}-lane-loop")
        if lane_loop_baseline is not None and lane_report is not None:
            passed, reasons = gate_lane(lane_report, lane_loop_baseline)
            output["lane_loop_gate"] = {"passed": passed, "reasons": reasons}
            gate_regressed = gate_regressed or not passed
            gated_any = True

        if not gated_any:
            # Fail loud instead of silently "passing": the caller asked to gate but
            # no guidance/region baseline exists to gate against.
            sys.stderr.write(
                f"no guidance/region baseline found for gate {args.gate!r} "
                f"(expected {args.gate}-guidance and/or {args.gate}-region).\n"
            )
            return EXIT_NO_PREDICTIONS

    text = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)

    if report.get("labeled_frames", 0) == 0:
        sys.stderr.write("manifest has no labeled ground truth; nothing to score.\n")
        return EXIT_NO_PREDICTIONS
    if gate_regressed:
        sys.stderr.write("REGRESSION: iPhone harness metrics worsened vs baseline; blocking.\n")
        return EXIT_REGRESSED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
