#!/usr/bin/env python3
"""Offline top-view preview for scene-hypothesis product gate (no Swift UI).

Mirrors SpatialTopProjection (x/z) and post-hypothesis styling:
- Photo floor tiles: teal (global) / yellow (local) — only if caller passes tiles
- Horizontal shells: gray dashed stroke, NO fill
- Optional legacy panel: teal fill on horizontals (pre-hypothesis bug)

Does not run SpatialSceneHypothesis; use iOS tests for publish logic.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw


def load_document(path: Path) -> dict:
    return json.loads(path.read_text())


def scope_epoch(document: dict) -> dict:
    keyframes = document.get("keyframes") or []
    epoch = None
    if keyframes:
        pose = keyframes[-1].get("pose") or {}
        epoch = pose.get("epoch")
    if epoch is None and document.get("surfaces"):
        epoch = document["surfaces"][-1].get("epoch")
    if epoch is None:
        return document
    out = dict(document)
    out["surfaces"] = [s for s in document.get("surfaces", []) if s.get("epoch") == epoch]
    out["poses"] = [p for p in document.get("poses", []) if p.get("epoch") == epoch]
    return out


def vertices(surface: dict) -> list[tuple[float, float, float]]:
    pts = []
    for v in surface.get("vertices") or []:
        if isinstance(v, dict):
            x, y, z = v.get("x"), v.get("y"), v.get("z")
        else:
            x, y, z = v[0], v[1], v[2]
        if x is None or y is None or z is None:
            continue
        if not all(math.isfinite(float(c)) for c in (x, y, z)):
            continue
        pts.append((float(x), float(y), float(z)))
    return pts


def polygon_area_xz(pts: list[tuple[float, float, float]]) -> float:
    if len(pts) < 3:
        return 0.0
    xs = [p[0] for p in pts]
    zs = [p[2] for p in pts]
    area = 0.0
    n = len(xs)
    for i in range(n):
        j = (i + 1) % n
        area += xs[i] * zs[j] - xs[j] * zs[i]
    return abs(area) * 0.5


class TopProjection:
    def __init__(self, points: list[tuple[float, float, float]], width: int, height: int):
        xs = [p[0] for p in points]
        zs = [p[2] for p in points]
        min_x, max_x = min(xs), max(xs)
        min_z, max_z = min(zs), max(zs)
        span_x = max(2.0, max_x - min_x)
        span_z = max(2.0, max_z - min_z)
        self.scale = min((width - 32) / span_x, (height - 32) / span_z)
        cx, cz = (min_x + max_x) / 2, (min_z + max_z) / 2
        self.offset_x = width / 2 - cx * self.scale
        self.offset_y = height / 2 - cz * self.scale

    def map_point(self, p: tuple[float, float, float]) -> tuple[float, float]:
        return (
            self.offset_x + p[0] * self.scale,
            self.offset_y + p[2] * self.scale,
        )


def draw_polygon(draw: ImageDraw.ImageDraw, proj: TopProjection, pts, *, fill=None, outline=None, width=1, dash=None):
    if len(pts) < 3:
        return
    screen = [proj.map_point(p) for p in pts]
    if dash:
        # PIL has no dashed polygon stroke; approximate with outline segments
        for i in range(len(screen)):
            a, b = screen[i], screen[(i + 1) % len(screen)]
            draw.line([a, b], fill=outline, width=width)
    else:
        draw.polygon(screen, fill=fill, outline=outline)


def render(document: dict, size: tuple[int, int], *, legacy_fill_horizontals: bool) -> tuple[Image.Image, dict]:
    w, h = size
    doc = scope_epoch(document)
    surfaces = [s for s in doc.get("surfaces", []) if s.get("observed")]
    all_pts = []
    for s in surfaces:
        all_pts.extend(vertices(s))
    for pose in doc.get("poses") or []:
        pos = pose.get("position") or pose
        if isinstance(pos, dict):
            all_pts.append((float(pos["x"]), float(pos["y"]), float(pos["z"])))
    if not all_pts:
        all_pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 1.0)]
    proj = TopProjection(all_pts, w, h)

    img = Image.new("RGB", (w, h), (18, 18, 20))
    draw = ImageDraw.Draw(img, "RGBA")

    horiz_area = 0.0
    horiz_count = 0
    for s in surfaces:
        if s.get("kind") != "horizontal":
            continue
        pts = vertices(s)
        horiz_area += polygon_area_xz(pts)
        horiz_count += 1
        if legacy_fill_horizontals:
            draw_polygon(draw, proj, pts, fill=(0, 128, 128, 82), outline=(0, 180, 180), width=1)
        else:
            draw_polygon(draw, proj, pts, outline=(140, 140, 140), width=1, dash=True)

    for s in surfaces:
        if s.get("kind") != "vertical":
            continue
        pts = vertices(s)
        draw_polygon(draw, proj, pts, outline=(160, 110, 220), width=2, dash=True)

    stats = {
        "horizontal_surfaces": horiz_count,
        "horizontal_shell_area_m2_xz": round(horiz_area, 2),
        "semantic_rows": len(document.get("indoorSemanticObservations") or []),
        "legacy_fill": legacy_fill_horizontals,
    }
    return img, stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("document", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--compare", action="store_true", help="Side-by-side legacy fill vs stroke-only")
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args()
    doc = load_document(args.document)
    if args.compare:
        left, stats_l = render(doc, (args.width // 2, args.height), legacy_fill_horizontals=True)
        right, stats_r = render(doc, (args.width // 2, args.height), legacy_fill_horizontals=False)
        out = Image.new("RGB", (args.width, args.height))
        out.paste(left, (0, 0))
        out.paste(right, (args.width // 2, 0))
        draw = ImageDraw.Draw(out)
        draw.text((8, 8), "legacy: horizontal fill", fill=(255, 200, 200))
        draw.text((args.width // 2 + 8, 8), "hypothesis: stroke only", fill=(200, 255, 200))
        stats = {"legacy": stats_l, "hypothesis": stats_r}
    else:
        out, stats = render(doc, (args.width, args.height), legacy_fill_horizontals=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.save(args.output)
    print(json.dumps({"output": str(args.output), **stats}, indent=2))


if __name__ == "__main__":
    main()
