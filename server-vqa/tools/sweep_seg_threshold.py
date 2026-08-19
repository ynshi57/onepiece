#!/usr/bin/env python3
"""Sweep the on-device ``seg_traversable_pixel`` threshold and measure the
missed_path ↔ false_go / hit_rate trade-off.

The threshold lives INSIDE the Swift engine (both the ROI cue and the guidance
centerline), so the only honest way to sweep it is to run the REAL macOS harness
once per threshold with a matching PerceptionConfig. This tool does exactly that:

    for t in thresholds:
        write config(seg_traversable_pixel=t) -> run harness -> eval vs manifest

It reports, per threshold:
- guidance line: both_ok, missed_path, false_go, hit_rate, mean_deviation, over_extension
- region status: status_accuracy, risk_miss, false_block

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

from app.guidance_path import GuidancePath, GuidancePathError  # noqa: E402
from app.guidance_path_eval import evaluate_guidance_paths  # noqa: E402
from app.path_dataset_eval import evaluate_path_guidance, load_jsonl  # noqa: E402
from app.perception_config import config_from_dict, default_config  # noqa: E402

DEFAULT_HARNESS = REPO_ROOT / "ios-vqa-app" / "perception-harness" / ".build" / "debug" / "PerceptionHarness"
DEFAULT_MODEL_DIR = REPO_ROOT / "ios-vqa-app" / "VQASee" / "VQASee"


def _write_config(threshold: float, path: Path) -> None:
    base = default_config().to_dict()
    base["thresholds"]["seg_traversable_pixel"] = float(threshold)
    cfg = config_from_dict(base)  # validates or raises
    path.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _guidance_pairs(manifest_rows, prediction_rows):
    preds = {}
    for row in prediction_rows:
        fid = row.get("frame_id")
        if fid is not None and isinstance(row.get("guidance_path"), dict):
            preds[fid] = row["guidance_path"]
    pairs = []
    for row in manifest_rows:
        fid = row.get("frame_id")
        gt_raw = row.get("ground_truth_path")
        pred_raw = preds.get(fid)
        if fid is None or not isinstance(gt_raw, dict) or pred_raw is None:
            continue
        try:
            pairs.append((fid, GuidancePath.from_dict(gt_raw), GuidancePath.from_dict(pred_raw)))
        except GuidancePathError:
            continue
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
        gl = evaluate_guidance_paths(_guidance_pairs(manifest_rows, pred_rows))
        region = evaluate_path_guidance(manifest_rows, pred_rows)
        rows.append({
            "threshold": t,
            "both_ok": gl["both_ok"],
            "missed_path": gl["missed_path_frames"],
            "false_go": gl["false_go_frames"],
            "hit_rate": gl["hit_rate"],
            "mean_deviation": gl["mean_deviation"],
            "over_extension": gl["over_extension"],
            "status_accuracy": region.get("status_accuracy"),
            "risk_miss": region.get("risk_miss_count"),
            "false_block": region.get("false_block_count"),
        })

    def fmt(v, nd=3):
        return f"{v:.{nd}f}" if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None else str(v)

    header = ["thr", "both_ok", "missed", "false_go", "hit_rate", "mean_dev", "over_ext",
              "status_acc", "risk_miss", "false_block"]
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join(["---"] * len(header)) + "|")
    for r in rows:
        print("| " + " | ".join([
            fmt(r["threshold"], 2), str(r["both_ok"]), str(r["missed_path"]), str(r["false_go"]),
            fmt(r["hit_rate"]), fmt(r["mean_deviation"]), fmt(r["over_extension"]),
            fmt(r["status_accuracy"]), str(r["risk_miss"]), str(r["false_block"]),
        ]) + " |")

    if args.out:
        args.out.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
