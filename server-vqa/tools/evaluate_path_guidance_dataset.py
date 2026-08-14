#!/usr/bin/env python3
"""Evaluate VQASee path guidance against an open/local dataset manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.path_dataset_eval import evaluate_path_guidance, load_jsonl  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate VQASee path guidance dataset manifest.")
    parser.add_argument("manifest", type=Path, help="JSONL manifest with ground_truth and optional prediction fields.")
    parser.add_argument("--predictions", type=Path, help="Optional JSONL predictions keyed by frame_id.")
    parser.add_argument("--output", type=Path, help="Write JSON report here instead of stdout.")
    args = parser.parse_args()

    manifest_rows = load_jsonl(args.manifest)
    prediction_rows = load_jsonl(args.predictions) if args.predictions else None
    report = evaluate_path_guidance(manifest_rows, prediction_rows)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
