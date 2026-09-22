"""Tests for the run_ios_harness_eval CLI: scoring harness predictions and the
regression gate that blocks a config candidate which worsens risk-miss.

The Swift harness itself needs macOS + Core ML and is validated separately; here
we test the platform-side scoring + gate logic with synthetic harness output so
it runs anywhere.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
TOOL = SERVER_ROOT / "tools" / "run_ios_harness_eval.py"

MANIFEST_ROWS = [
    {
        "frame_id": "f1",
        "traversable_grid": {"cols": 2, "rows": 2, "cells": [1, 0, 0, 0]},
    },
    {
        "frame_id": "f2",
        "traversable_grid": {"cols": 2, "rows": 2, "cells": [0, 1, 0, 0]},
    },
]


def _pred(frame_id, *, cells=None):
    row = {
        "frame_id": frame_id,
        "prediction": {
            "prediction_source": "ios_coreml_offline_harness",
        },
    }
    if cells is not None:
        row["traversable_grid"] = {"cols": len(cells[0]), "rows": len(cells), "cells": [v for r in cells for v in r]}
    return row


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _run(args, env_extra):
    import os

    env = os.environ.copy()
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_scores_perfect_predictions(tmp_path):
    manifest = tmp_path / "m.jsonl"
    preds = tmp_path / "p.jsonl"
    _write_jsonl(manifest, MANIFEST_ROWS)
    _write_jsonl(preds, [_pred("f1"), _pred("f2")])

    result = _run(["--manifest", str(manifest), "--predictions", str(preds)], {})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    report = payload["evaluation"]
    assert report["labeled_frames"] == 2
    assert "status_accuracy" not in report
    assert report["prediction_source"] == "ios_coreml_offline_harness"


def test_gate_without_region_baseline_fails_loud(tmp_path):
    """If the caller asks to gate but no region baseline exists, the tool must fail
    LOUD (EXIT_NO_PREDICTIONS) instead of silently passing."""
    baseline_dir = tmp_path / "baselines"
    manifest = tmp_path / "m.jsonl"
    good = tmp_path / "good.jsonl"
    _write_jsonl(manifest, MANIFEST_ROWS)
    _write_jsonl(good, [_pred("f1"), _pred("f2")])
    env = {"VQASEE_EVAL_BASELINE_DIR": str(baseline_dir)}

    # Saving produces no baseline when predictions carry no traversable_grid.
    save = _run(["--manifest", str(manifest), "--predictions", str(good), "--baseline", "ios-clean"], env)
    assert save.returncode == 0, save.stderr
    payload = json.loads(save.stdout)
    assert "baseline" not in payload  # legacy ROI baseline no longer written
    assert payload.get("baseline_note")

    gated = _run(["--manifest", str(manifest), "--predictions", str(good), "--gate", "ios-clean"], env)
    assert gated.returncode == 3, (gated.returncode, gated.stderr, gated.stdout)
    assert "no region baseline" in gated.stderr


def _grid(cells_rows):
    """Build a wire grid from a list of rows of 0/1."""
    rows = len(cells_rows)
    cols = len(cells_rows[0])
    flat = [int(v) for row in cells_rows for v in row]
    return {"cols": cols, "rows": rows, "cells": flat}


def test_region_gate_passes_when_grid_matches(tmp_path):
    baseline_dir = tmp_path / "baselines"
    manifest = tmp_path / "m.jsonl"
    clean = tmp_path / "clean.jsonl"
    grid = _grid([[1, 0], [1, 0]])
    _write_jsonl(manifest, [{"frame_id": "f1", "traversable_grid": grid}])
    good = _pred("f1")
    good["traversable_grid"] = grid
    _write_jsonl(clean, [good])

    env = {"VQASEE_EVAL_BASELINE_DIR": str(baseline_dir)}
    save = _run(["--manifest", str(manifest), "--predictions", str(clean), "--baseline", "rg"], env)
    assert save.returncode == 0, save.stderr
    gated = _run(["--manifest", str(manifest), "--predictions", str(clean), "--gate", "rg"], env)
    assert gated.returncode == 0, gated.stderr
    payload = json.loads(gated.stdout)
    assert payload["region_gate"]["passed"] is True


# --- Role-conditioned region evaluation (walk role: sidewalk vs road) ----------

def test_role_manifest_quantifies_road_as_sidewalk_defect(tmp_path):
    """With a walk-role manifest (sidewalk=primary, road=caution) the tool must
    report the four role metrics against the device walkable prediction, exposing
    how often the device would route a walker onto the road."""
    # GT: bottom row left cell = sidewalk (primary), right cell = road (caution).
    role_manifest = tmp_path / "walk.jsonl"
    role_row = {
        "frame_id": "f1",
        "role": "walk",
        "role_grids": {
            "primary": _grid([[1, 0]]),
            "caution": _grid([[0, 1]]),
            "lane": _grid([[0, 0]]),
            "obstacle": _grid([[0, 0]]),
        },
    }
    _write_jsonl(role_manifest, [role_row])

    manifest = tmp_path / "m.jsonl"
    _write_jsonl(manifest, [{"frame_id": "f1", "traversable_grid": _grid([[1, 0]])}])

    preds = tmp_path / "p.jsonl"
    p = _pred("f1")
    p["traversable_grid"] = _grid([[1, 1]])  # device calls BOTH sidewalk and road walkable
    _write_jsonl(preds, [p])

    result = _run(
        ["--manifest", str(manifest), "--predictions", str(preds), "--role-manifest", str(role_manifest)],
        {},
    )
    assert result.returncode == 0, result.stderr
    role = json.loads(result.stdout)["role"]
    assert role["scored"] == 1
    assert role["mean_primary_recall"] == 1.0            # covered the sidewalk
    assert role["mean_road_as_primary_rate"] == 0.5       # half of walkable is road
    assert role["road_as_primary_frames"] == 1            # flagged unsafe
