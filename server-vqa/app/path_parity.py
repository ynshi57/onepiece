"""Parity check: offline server predictor vs. on-device iOS prediction.

The server-side traversability predictor is an offline *proxy* for the real
on-device LocalPathGuidanceSignal. Its metrics are only trustworthy as a
relative trend if it does not diverge wildly from what the iPhone actually
produces. This module compares traversable-grid cells on frames they share
and raises a drift alert when disagreement exceeds a threshold.
"""

from __future__ import annotations

from typing import Any, Iterable

DEFAULT_DRIFT_THRESHOLD = 0.20


def _frame_id(row: dict[str, Any]) -> str:
    value = row.get("frame_id") or row.get("frame") or row.get("image")
    return str(value) if value is not None else ""


def _prediction(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("prediction", "path_guidance"):
        value = row.get(key)
        if isinstance(value, dict):
            merged = dict(value)
            break
    else:
        merged = {}
    if "traversable_grid" not in merged and isinstance(row.get("traversable_grid"), dict):
        merged["traversable_grid"] = row["traversable_grid"]
    return merged


def _grid_cells(pred: dict[str, Any]) -> tuple[Any, ...]:
    grid = pred.get("traversable_grid")
    if not isinstance(grid, dict):
        return ()
    cells = grid.get("cells")
    if not isinstance(cells, list):
        return ()
    return tuple(int(c) if isinstance(c, (int, float)) else 0 for c in cells)


def _lookup(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    table: dict[str, dict[str, Any]] = {}
    for row in rows:
        frame_id = _frame_id(row)
        if frame_id:
            table[frame_id] = _prediction(row)
    return table


def compute_parity(
    ios_rows: list[dict[str, Any]],
    server_rows: list[dict[str, Any]],
    *,
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
) -> dict[str, Any]:
    """Compare iOS vs server traversable grids on shared frames."""
    ios = _lookup(ios_rows)
    server = _lookup(server_rows)
    shared = sorted(set(ios) & set(server))

    mismatches: list[dict[str, str]] = []
    matched_frames = 0
    compared = 0

    for frame_id in shared:
        ios_cells = _grid_cells(ios[frame_id])
        server_cells = _grid_cells(server[frame_id])
        if not ios_cells and not server_cells:
            continue
        compared += 1
        if ios_cells == server_cells:
            matched_frames += 1
        else:
            mismatches.append(
                {
                    "frame_id": frame_id,
                    "field": "traversable_grid",
                    "ios": f"cells={len(ios_cells)}",
                    "server": f"cells={len(server_cells)}",
                }
            )

    overall_agreement = round(matched_frames / compared, 4) if compared else None
    drift_rate = round(1 - overall_agreement, 4) if overall_agreement is not None else None
    drift_alert = bool(drift_rate is not None and drift_rate > drift_threshold)

    return {
        "shared_frames": len(shared),
        "compared_fields": compared,
        "overall_agreement": overall_agreement,
        "drift_rate": drift_rate,
        "drift_threshold": drift_threshold,
        "drift_alert": drift_alert,
        "field_agreement": {
            "traversable_grid": overall_agreement,
        },
        "mismatches": mismatches[:100],
        "note": (
            "Server predictor is an offline proxy for on-device LocalPathGuidanceSignal; "
            "high drift means offline metrics should be read with caution."
        ),
    }
