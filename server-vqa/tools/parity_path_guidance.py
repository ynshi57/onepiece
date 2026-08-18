#!/usr/bin/env python3
"""Compare offline server predictions vs. on-device iOS predictions.

Both inputs are JSONL prediction files keyed by frame_id. The iOS file is
typically a diagnostic session's exported path manifest (its ``prediction``
field is the on-device LocalPathGuidanceSignal); the server file is produced by
``predict_path_guidance_dataset.py`` on the same frames.

    python server-vqa/tools/parity_path_guidance.py \
        --ios session-predictions.jsonl \
        --server server-predictions.jsonl

Exits non-zero when drift exceeds the threshold so CI can surface a divergence
between the offline proxy and the real device pipeline.
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
from app.path_parity import DEFAULT_DRIFT_THRESHOLD, compute_parity  # noqa: E402

EXIT_OK = 0
EXIT_DRIFT = 4


def main() -> int:
    parser = argparse.ArgumentParser(description="Parity check: server vs iOS path-guidance predictions.")
    parser.add_argument("--ios", type=Path, required=True, help="JSONL with on-device predictions (frame_id + prediction).")
    parser.add_argument("--server", type=Path, required=True, help="JSONL with server predictions (frame_id + prediction).")
    parser.add_argument("--drift-threshold", type=float, default=DEFAULT_DRIFT_THRESHOLD)
    parser.add_argument("--output", type=Path, help="Write parity report JSON here instead of stdout.")
    args = parser.parse_args()

    report = compute_parity(
        load_jsonl(args.ios),
        load_jsonl(args.server),
        drift_threshold=args.drift_threshold,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)

    if report["drift_alert"]:
        sys.stderr.write(
            f"drift alert: drift_rate={report['drift_rate']} > threshold={report['drift_threshold']}\n"
        )
        return EXIT_DRIFT
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
