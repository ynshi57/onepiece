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
from app.regression_gate import check_regression  # noqa: E402
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

EXIT_OK = 0
EXIT_NO_PREDICTIONS = 3
EXIT_REGRESSED = 4


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate iPhone on-device perception (harness output) against a manifest."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="GT manifest JSONL (has ground_truth per frame).")
    parser.add_argument("--predictions", type=Path, required=True, help="Harness prediction JSONL (frame_id + prediction).")
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
        saved = save_baseline(args.baseline, report, source="ios_coreml_offline_harness")
        output["baseline"] = str(saved)
        if guidance_report is not None:
            g_saved = save_baseline(
                f"{args.baseline}-guidance",
                {k: v for k, v in guidance_report.items() if k != "per_frame"},
                source="ios_coreml_offline_harness_guidance",
                metric_keys=GUIDANCE_BASELINE_KEYS,
            )
            output["guidance_baseline"] = str(g_saved)

    gate_regressed = False
    if args.gate:
        baseline = load_baseline(args.gate)
        if baseline is None:
            sys.stderr.write(f"gate baseline not found: {args.gate}\n")
            return EXIT_NO_PREDICTIONS
        gate = check_regression(report, baseline)
        output["gate"] = gate.as_dict()
        gate_regressed = not gate.passed

        # Also gate the guidance line against its own baseline, if present.
        g_baseline = load_baseline(f"{args.gate}-guidance")
        if g_baseline is not None and guidance_report is not None:
            passed, reasons = gate_guidance(guidance_report, g_baseline)
            output["guidance_gate"] = {"passed": passed, "reasons": reasons}
            gate_regressed = gate_regressed or not passed

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
