#!/usr/bin/env python3
"""Batch-predict path guidance over a VQASee dataset manifest.

Runs the offline server-side traversability predictor on every manifest row that
carries an ``image_path`` and writes a ``predictions.jsonl`` keyed by
``frame_id``. Feed the output straight into evaluation:

    python server-vqa/tools/predict_path_guidance_dataset.py \
        docs/datasets/camvid-manifest.jsonl \
        --output docs/datasets/camvid-predictions.jsonl
    python server-vqa/tools/evaluate_path_guidance_dataset.py \
        docs/datasets/camvid-manifest.jsonl \
        --predictions docs/datasets/camvid-predictions.jsonl

Honesty: if the predictor is unavailable (onnxruntime missing or model asset
missing) this exits non-zero with a clear reason instead of writing empty or
fabricated predictions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.path_dataset_eval import load_jsonl  # noqa: E402
from app.traversability_predictor import TraversabilityPredictor, predict_manifest  # noqa: E402

EXIT_OK = 0
EXIT_UNAVAILABLE = 2
EXIT_ALL_FAILED = 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-predict VQASee path guidance for a dataset manifest.")
    parser.add_argument("manifest", type=Path, help="JSONL manifest with image_path per row.")
    parser.add_argument("--model", type=Path, help="Path to traversability segmentation ONNX model.")
    parser.add_argument("--output", type=Path, help="Where to write predictions JSONL (default: <manifest>-predictions.jsonl).")
    parser.add_argument("--limit", type=int, default=0, help="Max frames to predict; 0 means all.")
    args = parser.parse_args()

    manifest_rows = load_jsonl(args.manifest)
    predictor = TraversabilityPredictor(model_path=args.model)
    result = predict_manifest(manifest_rows, predictor, limit=args.limit)

    capability = result["capability"]
    if capability.get("capability") != "active":
        sys.stderr.write(
            "predictor unavailable: "
            + str(capability.get("reason", ""))
            + "\n(no predictions written; this is explicit, not a silent skip)\n"
        )
        return EXIT_UNAVAILABLE

    output_path = args.output or args.manifest.with_name(args.manifest.stem + "-predictions.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in result["predictions"]:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    summary = {
        "manifest": str(args.manifest),
        "output": str(output_path),
        "predicted": result["predicted"],
        "errors": len(result["errors"]),
        "capability": capability,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if result["errors"]:
        sys.stderr.write(f"{len(result['errors'])} frame(s) failed; see errors below\n")
        for error in result["errors"][:20]:
            sys.stderr.write(f"  {error.get('frame_id', '')}: {error.get('error', '')}\n")

    if result["predicted"] == 0 and manifest_rows:
        return EXIT_ALL_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
