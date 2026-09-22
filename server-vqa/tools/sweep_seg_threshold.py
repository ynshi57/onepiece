#!/usr/bin/env python3
"""Sweep the on-device ``seg_traversable_pixel`` threshold and measure the
region IoU / recall / precision trade-off.

The threshold lives INSIDE the Swift engine (both the ROI cue and the guidance
centerline), so the only honest way to sweep it is to run the REAL macOS harness
once per threshold with a matching PerceptionConfig. This tool does exactly that:

    for t in thresholds:
        write config(seg_traversable_pixel=t) -> run harness -> eval vs manifest

It reports, per threshold:
- region grid: mean_iou, mean_recall, mean_precision, region_false_go_frames
- coverage: labeled_frames, missing_prediction_count

Requires macOS + a built harness binary (see --harness). If the binary is
missing this fails loudly with build instructions instead of pretending.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.path_dataset_eval import evaluate_path_guidance, load_jsonl  # noqa: E402
from app.perception_config import config_from_dict, default_config  # noqa: E402
from app.region_grid import evaluate_region_grids  # noqa: E402

DEFAULT_HARNESS = REPO_ROOT / "ios-vqa-app" / "perception-harness" / ".build" / "debug" / "PerceptionHarness"
DEFAULT_MODEL_DIR = REPO_ROOT / "ios-vqa-app" / "VQASee" / "VQASee"


def _write_config(threshold: float, path: Path) -> None:
    base = default_config().to_dict()
    base["thresholds"]["seg_traversable_pixel"] = float(threshold)
    cfg = config_from_dict(base)  # validates or raises
    path.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _region_pairs(manifest_rows, prediction_rows):
    preds = {}
    for row in prediction_rows:
        fid = row.get("frame_id")
        if fid is not None and isinstance(row.get("traversable_grid"), dict):
            preds[fid] = row["traversable_grid"]
    pairs = []
    for row in manifest_rows:
        fid = row.get("frame_id")
        gt_raw = row.get("traversable_grid")
        pred_raw = preds.get(fid)
        if fid is None or not isinstance(gt_raw, dict) or pred_raw is None:
            continue
        pairs.append((fid, gt_raw, pred_raw))
    return pairs


def _run_one(harness: Path, manifest: Path, model_dir: Path, config: Path, out: Path) -> None:
    result = subprocess.run(
        [str(harness), "--manifest", str(manifest), "--model-dir", str(model_dir),
         "--out", str(out), "--config", str(config)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"harness failed (rc={result.returncode}):\n{result.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--harness", type=Path, default=DEFAULT_HARNESS)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--thresholds", type=str, default="0.35,0.40,0.45,0.50,0.55,0.60,0.65")
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/vqasee-seg-sweep"))
    parser.add_argument("--out", type=Path, help="Write the summary JSON here.")
    args = parser.parse_args()

    if not args.harness.is_file():
        sys.stderr.write(
            f"harness binary not found: {args.harness}\n"
            "build it first:\n"
            "  cd ios-vqa-app/perception-harness && swift build\n"
        )
        return 2

    manifest_rows = load_jsonl(args.manifest)
    thresholds = [float(t) for t in args.thresholds.split(",") if t.strip()]
    args.work_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for t in thresholds:
        cfg_path = args.work_dir / f"config-{t:.2f}.json"
        preds_path = args.work_dir / f"preds-{t:.2f}.jsonl"
        _write_config(t, cfg_path)
        sys.stderr.write(f"[sweep] threshold={t:.2f} running harness...\n")
        _run_one(args.harness, args.manifest, args.model_dir, cfg_path, preds_path)

        pred_rows = load_jsonl(preds_path)
        region = evaluate_region_grids(_region_pairs(manifest_rows, pred_rows))
        status = evaluate_path_guidance(manifest_rows, pred_rows)
        rows.append({
            "threshold": t,
            "mean_iou": region.get("mean_iou"),
            "mean_recall": region.get("mean_recall"),
            "mean_precision": region.get("mean_precision"),
            "region_false_go": region.get("region_false_go_frames"),
            "region_miss": region.get("region_miss_frames"),
            "labeled_frames": status.get("labeled_frames"),
            "missing_predictions": status.get("missing_prediction_count"),
        })

    def fmt(v, nd=3):
        return f"{v:.{nd}f}" if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None else str(v)

    header = ["thr", "iou", "recall", "precision", "false_go", "miss", "labeled", "missing_pred"]
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join(["---"] * len(header)) + "|")
    for r in rows:
        print("| " + " | ".join([
            fmt(r["threshold"], 2), fmt(r["mean_iou"]), fmt(r["mean_recall"]), fmt(r["mean_precision"]),
            str(r["region_false_go"]), str(r["region_miss"]),
            str(r["labeled_frames"]), str(r["missing_predictions"]),
        ]) + " |")

    if args.out:
        args.out.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
