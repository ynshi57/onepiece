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
        "ground_truth": {
            "near_path_status": "blocked",
            "left_front_status": "candidateOpen",
            "right_front_status": "candidateOpen",
            "focus_direction": "center",
        },
    },
    {
        "frame_id": "f2",
        "ground_truth": {
            "near_path_status": "caution",
            "left_front_status": "candidateOpen",
            "right_front_status": "candidateOpen",
            "focus_direction": "unknown",
        },
    },
]


def _pred(frame_id, near):
    return {
        "frame_id": frame_id,
        "prediction": {
            "near_path_status": near,
            "left_front_status": "candidateOpen",
            "right_front_status": "candidateOpen",
            "focus_direction": "center" if near == "blocked" else "unknown",
            "prediction_source": "ios_coreml_offline_harness",
        },
    }


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
    _write_jsonl(preds, [_pred("f1", "blocked"), _pred("f2", "caution")])

    result = _run(["--manifest", str(manifest), "--predictions", str(preds)], {})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    report = payload["evaluation"]
    assert report["labeled_frames"] == 2
    assert report["status_accuracy"] == 1.0
    assert report["risk_miss_count"] == 0
    assert report["prediction_source"] == "ios_coreml_offline_harness"


def test_gate_blocks_when_risk_miss_worsens(tmp_path):
    baseline_dir = tmp_path / "baselines"
    manifest = tmp_path / "m.jsonl"
    good = tmp_path / "good.jsonl"
    bad = tmp_path / "bad.jsonl"
    _write_jsonl(manifest, MANIFEST_ROWS)
    # Good: matches GT (near f1 blocked, f2 caution) -> zero risk miss.
    _write_jsonl(good, [_pred("f1", "blocked"), _pred("f2", "caution")])
    # Bad: predicts candidateOpen where GT is blocked/caution -> risk misses.
    _write_jsonl(bad, [_pred("f1", "candidateOpen"), _pred("f2", "candidateOpen")])

    env = {"VQASEE_EVAL_BASELINE_DIR": str(baseline_dir)}

    # Save a clean baseline.
    save = _run(["--manifest", str(manifest), "--predictions", str(good), "--baseline", "ios-clean"], env)
    assert save.returncode == 0, save.stderr

    # Gate a regressed candidate against it -> non-zero exit + gate.passed False.
    gated = _run(["--manifest", str(manifest), "--predictions", str(bad), "--gate", "ios-clean"], env)
    assert gated.returncode == 4, (gated.returncode, gated.stderr, gated.stdout)
    payload = json.loads(gated.stdout)
    assert payload["gate"]["passed"] is False
    assert any("risk_miss" in v for v in payload["gate"]["violations"])


def test_gate_passes_when_not_worse(tmp_path):
    baseline_dir = tmp_path / "baselines"
    manifest = tmp_path / "m.jsonl"
    good = tmp_path / "good.jsonl"
    _write_jsonl(manifest, MANIFEST_ROWS)
    _write_jsonl(good, [_pred("f1", "blocked"), _pred("f2", "caution")])
    env = {"VQASEE_EVAL_BASELINE_DIR": str(baseline_dir)}

    _run(["--manifest", str(manifest), "--predictions", str(good), "--baseline", "ios-clean"], env)
    gated = _run(["--manifest", str(manifest), "--predictions", str(good), "--gate", "ios-clean"], env)
    assert gated.returncode == 0, gated.stderr
    payload = json.loads(gated.stdout)
    assert payload["gate"]["passed"] is True
