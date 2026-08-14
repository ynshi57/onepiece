#!/usr/bin/env python3
"""Extract frames from videos into a VQASee dataset folder and manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract video frames for VQASee path-guidance evaluation.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--every", type=int, default=30, help="Save every Nth video frame.")
    parser.add_argument("--split", default="video")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()

    if not args.video.is_file():
        raise SystemExit(f"video not found: {args.video}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"failed to open video: {args.video}")

    rows = []
    frame_index = 0
    saved = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % max(1, args.every) == 0:
            name = f"{args.video.stem}-frame-{frame_index:06d}.jpg"
            out_path = args.output_dir / name
            cv2.imwrite(str(out_path), frame)
            rows.append({
                "frame_id": f"{args.split}/{args.video.stem}/{frame_index:06d}",
                "image": str(out_path),
                "split": args.split,
                "scene_tags": args.tag,
                "ground_truth": {},
            })
            saved += 1
            if args.max_frames and saved >= args.max_frames:
                break
        frame_index += 1
    cap.release()
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    print(f"Extracted {saved} frames to {args.output_dir}; manifest={args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
