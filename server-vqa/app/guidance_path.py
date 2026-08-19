"""Guidance-path representation + centerline generator.

This is the shared "traversable guidance line" schema for VQASee. The on-device
`LocalPathGuidanceEngine` (Swift) produces predicted guidance lines from its
segmentation + object perception; the server produces ground-truth lines from a
dataset traversability mask. Both sides speak the SAME wire schema so the closed
loop can score them fairly.

Coordinate convention (matches ROIs / object boxes elsewhere in the pipeline):
normalized image coordinates, origin BOTTOM-LEFT, y up. A guidance line starts
near the user's feet (small y, bottom of frame) and extends forward (larger y).

The Swift mirror lives in
`ios-vqa-app/VQASee/VQASee/GuidancePath.swift`; keep the wire keys / defaults in
sync (guarded by `tests/test_guidance_path_swift_parity.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


class GuidancePathError(ValueError):
    """Raised on schema / range violations (never silently clamped)."""


PATH_STATUS_OK = "ok"
PATH_STATUS_INSUFFICIENT = "insufficient"
_VALID_STATUS = {PATH_STATUS_OK, PATH_STATUS_INSUFFICIENT}
_VALID_KIND = {"primary", "alternative"}

# A line needs at least this vertical coverage AND this many points to be "ok".
MIN_COVERAGE = 0.30
MIN_POINTS = 3


def _check_unit(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or not (0.0 <= float(value) <= 1.0):
        raise GuidancePathError(f"{name}={value!r} not in [0,1]")
    return float(value)


@dataclass
class GuidancePoint:
    x: float          # lateral center, normalized [0,1]
    y: float          # forward distance, normalized [0,1] (bottom-left origin)
    half_width: float  # half corridor width at this point, normalized [0,1]

    def to_dict(self) -> dict[str, float]:
        return {"x": round(self.x, 5), "y": round(self.y, 5), "half_width": round(self.half_width, 5)}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GuidancePoint":
        return GuidancePoint(
            x=_check_unit("point.x", data.get("x")),
            y=_check_unit("point.y", data.get("y")),
            half_width=_check_unit("point.half_width", data.get("half_width", 0.0)),
        )


@dataclass
class RiskSegment:
    from_index: int
    to_index: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"from_index": self.from_index, "to_index": self.to_index, "reason": self.reason}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "RiskSegment":
        return RiskSegment(
            from_index=int(data.get("from_index", 0)),
            to_index=int(data.get("to_index", 0)),
            reason=str(data.get("reason", "")),
        )


@dataclass
class GuidanceLine:
    points: list[GuidancePoint]
    confidence: float = 0.0
    kind: str = "primary"
    risk_segments: list[RiskSegment] = field(default_factory=list)

    def validate(self) -> None:
        if self.kind not in _VALID_KIND:
            raise GuidancePathError(f"unknown line kind: {self.kind!r}")
        _check_unit("line.confidence", self.confidence)
        prev_y = -1.0
        for point in self.points:
            # Points must be ordered from feet (small y) forward (large y).
            if point.y < prev_y - 1e-6:
                raise GuidancePathError("line points must be ordered by y ascending")
            prev_y = point.y
        for seg in self.risk_segments:
            if not (0 <= seg.from_index <= seg.to_index < max(1, len(self.points))):
                raise GuidancePathError(f"risk segment out of range: {seg.to_dict()}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "confidence": round(self.confidence, 5),
            "points": [p.to_dict() for p in self.points],
            "risk_segments": [s.to_dict() for s in self.risk_segments],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GuidanceLine":
        line = GuidanceLine(
            points=[GuidancePoint.from_dict(p) for p in data.get("points", [])],
            confidence=_check_unit("line.confidence", data.get("confidence", 0.0)),
            kind=str(data.get("kind", "primary")),
            risk_segments=[RiskSegment.from_dict(s) for s in data.get("risk_segments", [])],
        )
        line.validate()
        return line


@dataclass
class GuidancePath:
    status: str
    coverage: float
    lines: list[GuidanceLine] = field(default_factory=list)
    source: str = ""

    def validate(self) -> None:
        if self.status not in _VALID_STATUS:
            raise GuidancePathError(f"unknown path status: {self.status!r}")
        _check_unit("coverage", self.coverage)
        for line in self.lines:
            line.validate()

    @property
    def primary(self) -> GuidanceLine | None:
        for line in self.lines:
            if line.kind == "primary":
                return line
        return self.lines[0] if self.lines else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "coverage": round(self.coverage, 5),
            "source": self.source,
            "lines": [line.to_dict() for line in self.lines],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GuidancePath":
        path = GuidancePath(
            status=str(data.get("status", PATH_STATUS_INSUFFICIENT)),
            coverage=_check_unit("coverage", data.get("coverage", 0.0)),
            lines=[GuidanceLine.from_dict(line) for line in data.get("lines", [])],
            source=str(data.get("source", "")),
        )
        path.validate()
        return path


# ---------------------------------------------------------------------------
# Centerline generation (shared algorithm; GT uses a boolean mask, the on-device
# engine mirrors this on its segmentation grid in Swift).
# ---------------------------------------------------------------------------

def _runs(row: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True runs in a boolean row as (start, end_exclusive)."""
    runs: list[tuple[int, int]] = []
    start = None
    for i, val in enumerate(row):
        if val and start is None:
            start = i
        elif not val and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(row)))
    return runs


def centerline_from_mask(
    mask: np.ndarray,
    *,
    samples: int = 16,
    horizon: float = 0.55,
    source: str = "",
) -> GuidancePath:
    """Trace a free-space centerline through a boolean traversability mask.

    - `mask`: (H, W) boolean, top-left origin (numpy image convention).
    - `samples`: number of rows sampled from the bottom up.
    - `horizon`: fraction of image height (from the bottom) to trace into.

    Returns a `GuidancePath` in bottom-left-origin normalized coordinates. If the
    free space is too broken to form a line, returns status=insufficient (an
    explicit degrade, never a fabricated straight line).

    Tracing rule (safety-aware): leading blocked rows at the BOTTOM are skipped to
    find the start anchor (a driving frame's hood / immediate foreground must not
    kill an otherwise clear path), but an interior gap once tracing has started
    breaks the line — we never bridge across an obstacle ahead.
    """
    if mask.ndim != 2:
        raise GuidancePathError(f"mask must be 2D, got shape {mask.shape}")
    height, width = mask.shape
    if height < 2 or width < 2:
        return GuidancePath(status=PATH_STATUS_INSUFFICIENT, coverage=0.0, source=source)

    top_row = int(round(height * (1.0 - min(max(horizon, 0.05), 1.0))))
    top_row = max(0, min(top_row, height - 2))
    # Sample image rows from the bottom (height-1) up to the horizon row.
    row_indices = np.linspace(height - 1, top_row, num=max(2, samples)).round().astype(int)

    points: list[GuidancePoint] = []
    prev_center: float | None = None
    for img_row in row_indices:
        row_runs = _runs(mask[img_row])
        if not row_runs:
            if prev_center is None:
                # Skip leading blocked rows at the bottom (e.g. a car hood or the
                # immediate foreground in a driving frame) until the first
                # traversable row anchors the line.
                continue
            # Interior gap = a real obstacle ahead. Stop here and NEVER bridge
            # across it, or we would draw a path straight through the obstacle.
            break
        target = width * 0.5 if prev_center is None else prev_center
        best = min(row_runs, key=lambda r: abs(((r[0] + r[1]) / 2.0) - target))
        center = (best[0] + best[1]) / 2.0
        half_w = (best[1] - best[0]) / 2.0
        prev_center = center
        points.append(
            GuidancePoint(
                x=center / width,
                y=1.0 - (float(img_row) + 0.5) / height,
                half_width=half_w / width,
            )
        )

    coverage = len(points) / float(len(row_indices))
    if len(points) < MIN_POINTS or coverage < MIN_COVERAGE:
        return GuidancePath(
            status=PATH_STATUS_INSUFFICIENT, coverage=round(coverage, 5), source=source
        )

    # Points were appended bottom->up (increasing y) already.
    confidence = round(min(1.0, coverage), 5)
    line = GuidanceLine(points=points, confidence=confidence, kind="primary")
    return GuidancePath(
        status=PATH_STATUS_OK, coverage=round(coverage, 5), lines=[line], source=source
    )
