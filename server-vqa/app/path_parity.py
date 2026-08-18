"""Parity check: offline server predictor vs. on-device iOS prediction.

The server-side traversability predictor is an offline *proxy* for the real
on-device LocalPathGuidanceSignal. Its metrics are only trustworthy as a
relative trend if it does not diverge wildly from what the iPhone actually
produces. This module compares the two prediction sources on frames they share
and raises a drift alert when disagreement exceeds a threshold.

Both inputs are lists of prediction rows keyed by ``frame_id``, each carrying a
``prediction`` (or ``path_guidance``) dict with the standard path-guidance
fields.
"""

from __future__ import annotations

from typing import Any, Iterable

PARITY_FIELDS = ("near_path_status", "left_front_status", "right_front_status", "focus_direction")
DEFAULT_DRIFT_THRESHOLD = 0.20


def _frame_id(row: dict[str, Any]) -> str:
    value = row.get("frame_id") or row.get("frame") or row.get("image")
    return str(value) if value is not None else ""


def _prediction(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("prediction", "path_guidance"):
        value = row.get(key)
        if isinstance(value, dict):
            return value
    return {}


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
    """Compare iOS vs server predictions on shared frames.

    Returns per-field agreement, overall agreement, drift rate, a drift alert
    flag (drift_rate > threshold), and up to 100 concrete mismatches so a human
    can inspect where the offline proxy diverges from the device.
    """
    ios = _lookup(ios_rows)
    server = _lookup(server_rows)
    shared = sorted(set(ios) & set(server))

    field_matches = {field: 0 for field in PARITY_FIELDS}
    field_totals = {field: 0 for field in PARITY_FIELDS}
    mismatches: list[dict[str, str]] = []
    total_fields = 0
    matched_fields = 0

    for frame_id in shared:
        ios_pred = ios[frame_id]
        server_pred = server[frame_id]
        for field in PARITY_FIELDS:
            ios_value = str(ios_pred.get(field, "unknown"))
            server_value = str(server_pred.get(field, "unknown"))
            field_totals[field] += 1
            total_fields += 1
            if ios_value == server_value:
                field_matches[field] += 1
                matched_fields += 1
            else:
                mismatches.append(
                    {
                        "frame_id": frame_id,
                        "field": field,
                        "ios": ios_value,
                        "server": server_value,
                    }
                )

    overall_agreement = round(matched_fields / total_fields, 4) if total_fields else None
    drift_rate = round(1 - overall_agreement, 4) if overall_agreement is not None else None
    field_agreement = {
        field: (round(field_matches[field] / field_totals[field], 4) if field_totals[field] else None)
        for field in PARITY_FIELDS
    }
    drift_alert = bool(drift_rate is not None and drift_rate > drift_threshold)

    return {
        "shared_frames": len(shared),
        "compared_fields": total_fields,
        "overall_agreement": overall_agreement,
        "drift_rate": drift_rate,
        "drift_threshold": drift_threshold,
        "drift_alert": drift_alert,
        "field_agreement": field_agreement,
        "mismatches": mismatches[:100],
        "note": (
            "Server predictor is an offline proxy for on-device LocalPathGuidanceSignal; "
            "high drift means offline metrics should be read with caution."
        ),
    }
