#!/usr/bin/env python3
"""Create a VQASee path-guidance manifest from images and traversability masks."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.path_dataset_import import create_manifest_from_folders, row_for_image  # noqa: E402,F401


def main() -> int:
    parser = argparse.ArgumentParser(description="Create VQASee path guidance manifest from image/mask folders.")
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--masks", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", default="indoor")
    parser.add_argument("--tag", action="append", default=[], help="Scene tag; can be repeated.")
    parser.add_argument("--threshold", type=float, default=0.5, help="Mask value threshold treated as traversable.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    rows = create_manifest_from_folders(
        images_dir=args.images,
        masks_dir=args.masks,
        output_path=args.output,
        split=args.split,
        scene_tags=args.tag,
        threshold=args.threshold,
        limit=args.limit,
    )
    print(f"Wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
