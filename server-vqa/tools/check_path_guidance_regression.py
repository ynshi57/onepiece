#!/usr/bin/env python3
"""Fail (non-zero exit) when path-guidance quality regresses vs a baseline.

Evaluate a manifest (optionally with external predictions) and compare it to a
saved baseline. Intended as a release/merge gate:

    python server-vqa/tools/check_path_guidance_regression.py \
        docs/datasets/camvid-manifest.jsonl \
        --baseline camvid-manifest \
        --predictions docs/datasets/camvid-predictions.jsonl

Exit codes: 0 pass, 5 regression detected, 6 baseline not found.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.eval_baseline import load_baseline  # noqa: E402
from app.path_dataset_eval import evaluate_path_guidance, load_jsonl  # noqa: E402
from app.regression_gate import GateThresholds, check_regression  # noqa: E402

EXIT_OK = 0
EXIT_REGRESSION = 5
EXIT_NO_BASELINE = 6


def main() -> int:
    parser = argparse.ArgumentParser(description="Regression gate for VQASee path guidance.")
    parser.add_argument("manifest", type=Path, help="JSONL manifest with ground_truth (and optional prediction).")
    parser.add_argument("--baseline", required=True, help="Name of the saved baseline to compare against.")
    parser.add_argument("--predictions", type=Path, help="Optional external predictions JSONL keyed by frame_id.")
    parser.add_argument("--max-status-accuracy-drop", type=float, default=0.02)
    parser.add_argument("--max-direction-accuracy-drop", type=float, default=0.02)
    parser.add_argument("--max-risk-miss-increase", type=int, default=0)
    parser.add_argument("--max-false-block-increase", type=int, default=0)
    parser.add_argument("--max-unknown-rate-increase", type=float, default=0.05)
    args = parser.parse_args()

    baseline = load_baseline(args.baseline)
    if baseline is None:
        sys.stderr.write(f"baseline not found: {args.baseline}. Save one first via the platform or dataset baseline API.\n")
        return EXIT_NO_BASELINE

    manifest_rows = load_jsonl(args.manifest)
    prediction_rows = load_jsonl(args.predictions) if args.predictions else None
    current = evaluate_path_guidance(manifest_rows, prediction_rows)

    thresholds = GateThresholds(
        max_status_accuracy_drop=args.max_status_accuracy_drop,
        max_direction_accuracy_drop=args.max_direction_accuracy_drop,
        max_risk_miss_increase=args.max_risk_miss_increase,
        max_false_block_increase=args.max_false_block_increase,
        max_unknown_rate_increase=args.max_unknown_rate_increase,
    )
    result = check_regression(current, baseline, thresholds)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))

    if not result.passed:
        sys.stderr.write("REGRESSION DETECTED:\n")
        for violation in result.violations:
            sys.stderr.write(f"  - {violation}\n")
        return EXIT_REGRESSION
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
