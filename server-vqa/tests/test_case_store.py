"""Tests for the closed-loop case layer (case_store).

Cover the properties the case layer exists for:
- failure classification matches the diagnostic viewer (no UI/case drift),
- clustering is deterministic and idempotent (re-run updates, never duplicates),
- the lifecycle state machine + auto-reopen actually fire,
- invalid transitions and unknown cases fail loudly (no silent failure).
"""

from __future__ import annotations

import pytest

from app import case_store
from app.case_store import (
    cluster_failures,
    dataset_key_from_manifest,
    frame_failure_types,
    list_cases,
    load_case,
    make_case_id,
    set_status,
    upsert_clusters,
)
from app.diagnostic_api import _frame_flags


@pytest.fixture(autouse=True)
def _isolated_case_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VQASEE_CASE_DIR", str(tmp_path / "cases"))
    yield


def _row(fid, near, left, right):
    return {
        "frame_id": fid,
        "ground_truth": {
            "near_path_status": near[0],
            "left_front_status": left[0],
            "right_front_status": right[0],
        },
    }


def _pred(fid, near, left, right):
    return {
        "frame_id": fid,
        "prediction": {
            "near_path_status": near,
            "left_front_status": left,
            "right_front_status": right,
        },
    }


# --- classification -------------------------------------------------------


def test_risk_miss_when_truth_blocked_but_pred_open():
    gt = {"near_path_status": "blocked", "left_front_status": "candidateOpen", "right_front_status": "candidateOpen"}
    pred = {"near_path_status": "candidateOpen", "left_front_status": "candidateOpen", "right_front_status": "candidateOpen"}
    assert frame_failure_types(gt, pred) == {"risk_miss"}


def test_false_block_when_truth_open_but_pred_caution():
    gt = {"near_path_status": "candidateOpen", "left_front_status": "candidateOpen", "right_front_status": "candidateOpen"}
    pred = {"near_path_status": "caution", "left_front_status": "candidateOpen", "right_front_status": "candidateOpen"}
    assert frame_failure_types(gt, pred) == {"false_block"}


def test_no_failure_when_equal_or_missing_prediction():
    gt = {"near_path_status": "caution", "left_front_status": "candidateOpen", "right_front_status": "candidateOpen"}
    assert frame_failure_types(gt, gt) == set()
    assert frame_failure_types(gt, {}) == set()


def test_case_classification_agrees_with_viewer_flags():
    """The case layer and the per-frame UI must never disagree on what is a
    failure — they share frame_failure_types by construction; assert it."""
    gt = {"near_path_status": "blocked", "left_front_status": "candidateOpen", "right_front_status": "candidateOpen"}
    pred = {"near_path_status": "candidateOpen", "left_front_status": "caution", "right_front_status": "candidateOpen"}
    case_types = frame_failure_types(gt, pred)
    ui_flags = _frame_flags(gt, pred)
    assert case_types == {"risk_miss", "false_block"}
    assert case_types.issubset(ui_flags)


# --- clustering + deterministic id ---------------------------------------


def test_cluster_groups_by_failure_type_with_deterministic_ids():
    manifest = [
        _row("a", ("blocked",), ("candidateOpen",), ("candidateOpen",)),
        _row("b", ("caution",), ("candidateOpen",), ("candidateOpen",)),
        _row("c", ("candidateOpen",), ("candidateOpen",), ("candidateOpen",)),
    ]
    preds = {
        "a": _pred("a", "candidateOpen", "candidateOpen", "candidateOpen"),  # risk_miss
        "b": _pred("b", "candidateOpen", "candidateOpen", "candidateOpen"),  # risk_miss
        "c": _pred("c", "caution", "candidateOpen", "candidateOpen"),        # false_block
    }
    clusters = cluster_failures(manifest, preds, dataset_key="camvid-manifest")
    by_type = {c["failure_type"]: c for c in clusters}
    assert set(by_type) == {"risk_miss", "false_block"}
    assert by_type["risk_miss"]["case_id"] == make_case_id("camvid-manifest", "risk_miss")
    assert by_type["risk_miss"]["frame_ids"] == ["a", "b"]
    assert by_type["risk_miss"]["frame_count"] == 2
    assert by_type["false_block"]["frame_ids"] == ["c"]


def test_dataset_key_is_path_independent():
    assert dataset_key_from_manifest("/a/b/camvid-manifest.jsonl") == "camvid-manifest"
    assert dataset_key_from_manifest("/other/camvid-manifest.jsonl") == "camvid-manifest"


# --- persistence + idempotency -------------------------------------------


def test_upsert_creates_then_updates_without_duplicating():
    manifest = [_row("a", ("blocked",), ("candidateOpen",), ("candidateOpen",))]
    preds = {"a": _pred("a", "candidateOpen", "candidateOpen", "candidateOpen")}
    clusters = cluster_failures(manifest, preds, dataset_key="ds")

    first = upsert_clusters(clusters, source="run1")
    assert len(first) == 1
    assert first[0]["status"] == "new"
    assert len(list_cases()) == 1

    # Re-run with two failing frames: same case id, updated count, no new file.
    manifest.append(_row("b", ("caution",), ("candidateOpen",), ("candidateOpen",)))
    preds["b"] = _pred("b", "candidateOpen", "candidateOpen", "candidateOpen")
    clusters2 = cluster_failures(manifest, preds, dataset_key="ds")
    second = upsert_clusters(clusters2, source="run2")
    assert len(list_cases()) == 1  # still one case, not duplicated
    case = load_case(second[0]["case_id"])
    assert case["frame_count"] == 2
    assert case["frame_ids"] == ["a", "b"]
    events = [h["event"] for h in case["history"]]
    assert events == ["opened", "recluster"]


# --- lifecycle + auto-reopen ---------------------------------------------


def test_verified_case_reopens_only_on_regression_not_stable_residual():
    """A verified case with an accepted residual must NOT reopen when the same
    residual persists, but MUST reopen when the problem regresses (more frames)."""
    manifest = [_row("a", ("blocked",), ("candidateOpen",), ("candidateOpen",))]
    preds = {"a": _pred("a", "candidateOpen", "candidateOpen", "candidateOpen")}
    clusters = cluster_failures(manifest, preds, dataset_key="ds")
    cid = upsert_clusters(clusters, source="run1")[0]["case_id"]

    # Human verifies the fix, accepting the current 1-frame residual as the bar.
    set_status(cid, "verified", note="fixed by sampler patch")
    assert load_case(cid)["resolved_frame_count"] == 1

    # Same residual on a later run -> stays verified (no nagging reopen).
    stable = upsert_clusters(clusters, source="run2")
    assert stable[0]["status"] == "verified"
    assert load_case(cid)["history"][-1]["event"] == "recluster"

    # Now the problem regresses (2 frames > accepted 1) -> must reopen.
    manifest.append(_row("b", ("caution",), ("candidateOpen",), ("candidateOpen",)))
    preds["b"] = _pred("b", "candidateOpen", "candidateOpen", "candidateOpen")
    clusters2 = cluster_failures(manifest, preds, dataset_key="ds")
    reopened = upsert_clusters(clusters2, source="run3")
    assert reopened[0]["status"] == "reopened"
    assert load_case(cid)["history"][-1]["event"] == "reopened"


def test_working_status_is_preserved_on_recluster():
    manifest = [_row("a", ("blocked",), ("candidateOpen",), ("candidateOpen",))]
    preds = {"a": _pred("a", "candidateOpen", "candidateOpen", "candidateOpen")}
    clusters = cluster_failures(manifest, preds, dataset_key="ds")
    cid = upsert_clusters(clusters, source="run1")[0]["case_id"]
    set_status(cid, "fixing")
    again = upsert_clusters(clusters, source="run2")
    # fixing is not a resolved state, so it must NOT reopen.
    assert again[0]["status"] == "fixing"


def test_invalid_status_and_unknown_case_fail_loudly():
    manifest = [_row("a", ("blocked",), ("candidateOpen",), ("candidateOpen",))]
    preds = {"a": _pred("a", "candidateOpen", "candidateOpen", "candidateOpen")}
    cid = upsert_clusters(cluster_failures(manifest, preds, dataset_key="ds"), source="r")[0]["case_id"]
    with pytest.raises(ValueError):
        set_status(cid, "not-a-status")
    with pytest.raises(ValueError):
        set_status("ds:does-not-exist", "triaged")
