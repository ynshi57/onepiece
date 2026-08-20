"""VQASee closed-loop *case* layer (MVP).

A **case** is the persistent carrier for a recurring quality problem, borrowed
(降维) from the DCL "clip 统一载体 + 生命周期状态机 + AutoTriage" architecture — see
``docs/tech-radar/2026-08-20-dcl-data-closed-loop-architecture.md``.

Motivation (AGENTS 原则): "一个问题如果发生两次，就不只是 bug，而是系统没有学会。"
Aggregate eval metrics and free-form model-lab notes cannot answer "did this exact
failure come back?". A case can, because it has:

- a **deterministic id** derived from (dataset, failure_type) → the same failure
  bucket always maps to the same case, so re-running eval *updates* instead of
  duplicating (idempotent, like DCL's deterministic ``clip_id``);
- an explicit **lifecycle state machine** (new → triaged → fixing → verified →
  released / closed), so progress is observable and auditable;
- **auto-reopen**: if a previously verified/closed case shows failing frames again,
  it flips back to ``reopened`` and records it — the "发生两次就自动重开" rule.

Storage is intentionally lightweight: one JSON file per case under a local
directory (mirrors ``eval_baseline`` and DCL's per-clip snapshot). We store only
frame ids (short relative strings like ``road/0001TP_007020``) and scalar
metadata — never images, absolute paths, or raw model output — so cases are safe
to commit.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Canonical per-frame failure classification (region level).
#
# This is the single source of truth for "what counts as a failure frame". The
# per-frame diagnostic viewer (``diagnostic_api._frame_flags``) reuses it so the
# UI filters and the case clusters can never drift apart.
# ---------------------------------------------------------------------------

REGION_KEYS = ("near_path_status", "left_front_status", "right_front_status")

# Failure types the case layer clusters on today. Guidance-line level failures
# (missed_path / false_go) are aggregate-only for now and intentionally NOT
# clustered here yet — see the tech-radar card for the follow-up.
FAILURE_TYPES = ("risk_miss", "false_block")

_FAILURE_LABEL = {
    "risk_miss": "漏报风险",
    "false_block": "误阻挡",
}
_FAILURE_HINT = {
    "risk_miss": "真实 注意/占用，却预测 可走候选（最危险）",
    "false_block": "真实 可走，却预测 注意/占用（过度保守）",
}

# Lifecycle state machine. Order encodes normal forward progress; ``reopened``
# and ``closed`` are the two off-ramp states.
CASE_STATUSES = (
    "new",
    "triaged",
    "fixing",
    "verified",
    "released",
    "reopened",
    "closed",
)

# Statuses that mean "we believed this was resolved". If failing frames come back
# while a case sits in one of these, we auto-reopen it.
_RESOLVED_STATUSES = frozenset({"verified", "released", "closed"})

_MAX_STORED_FRAME_IDS = 2000

_SAFE_ID = re.compile(r"[^A-Za-z0-9._:-]+")


def frame_failure_types(gt: dict, prediction: dict) -> set[str]:
    """Return the set of region-level failure types for one frame.

    Empty prediction or missing regions never *invent* a failure; a frame only
    fails when both ground truth and prediction are present and disagree in a
    safety-relevant way. Mirrors ``diagnostic_api._frame_flags`` exactly for the
    ``risk_miss`` / ``false_block`` buckets."""
    if not prediction:
        return set()
    out: set[str] = set()
    for key in REGION_KEYS:
        g = gt.get(key)
        p = prediction.get(key)
        if g is None or p is None:
            continue
        if g in ("caution", "blocked") and p == "candidateOpen":
            out.add("risk_miss")
        elif g == "candidateOpen" and p in ("caution", "blocked"):
            out.add("false_block")
    return out


# ---------------------------------------------------------------------------
# Store location + id helpers
# ---------------------------------------------------------------------------


def case_root() -> Path:
    configured = os.getenv("VQASEE_CASE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "cases"


def dataset_key_from_manifest(manifest_path: str | Path) -> str:
    """Stable, path-independent key for a dataset: the manifest file stem.

    Two people running the same ``camvid-manifest.jsonl`` from different absolute
    paths must land on the same case id, so we deliberately drop the directory."""
    stem = Path(str(manifest_path)).name
    if stem.endswith(".jsonl"):
        stem = stem[: -len(".jsonl")]
    elif stem.endswith(".json"):
        stem = stem[: -len(".json")]
    return _safe_component(stem) or "dataset"


def make_case_id(dataset_key: str, failure_type: str) -> str:
    """Deterministic id: ``{dataset_key}:{failure_type}``. Same failure bucket →
    same id → idempotent upsert (no duplicate cases on re-run)."""
    return f"{_safe_component(dataset_key)}:{_safe_component(failure_type)}"


def _safe_component(text: str) -> str:
    return _SAFE_ID.sub("-", str(text).strip()).strip("-.:")


def _case_path(case_id: str) -> Path:
    # Colons are fine on POSIX filenames but noisy; use a filesystem-safe form.
    safe = case_id.replace(":", "__")
    return case_root() / f"{safe}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Clustering: eval run -> proposed case clusters
# ---------------------------------------------------------------------------


def cluster_failures(
    manifest_rows: list[dict],
    pred_index: dict[str, dict],
    *,
    dataset_key: str,
    failure_types: tuple[str, ...] = FAILURE_TYPES,
) -> list[dict]:
    """Group failing frames from one eval run into proposed case clusters.

    ``pred_index`` maps ``frame_id`` -> a prediction row (the object holding a
    ``prediction`` dict, matching the harness output shape). Returns one cluster
    dict per failure type that has at least one frame, each with a deterministic
    ``case_id`` and the sorted list of failing ``frame_ids``."""
    buckets: dict[str, list[str]] = {ft: [] for ft in failure_types}
    for row in manifest_rows:
        fid = str(row.get("frame_id", ""))
        if not fid:
            continue
        gt = row.get("ground_truth", {}) or {}
        pred_row = pred_index.get(fid) or {}
        prediction = pred_row.get("prediction", {}) or {}
        types = frame_failure_types(gt, prediction)
        for ft in failure_types:
            if ft in types:
                buckets[ft].append(fid)

    clusters: list[dict] = []
    for ft in failure_types:
        frame_ids = sorted(buckets[ft])
        if not frame_ids:
            continue
        clusters.append(
            {
                "case_id": make_case_id(dataset_key, ft),
                "dataset_key": dataset_key,
                "failure_type": ft,
                "title": f"{_FAILURE_LABEL.get(ft, ft)}簇 · {dataset_key}",
                "hint": _FAILURE_HINT.get(ft, ""),
                "frame_count": len(frame_ids),
                "frame_ids": frame_ids,
            }
        )
    return clusters


# ---------------------------------------------------------------------------
# Persistence + lifecycle
# ---------------------------------------------------------------------------


def load_case(case_id: str) -> dict[str, Any] | None:
    path = _case_path(case_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt case at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"case at {path} is not an object")
    return value


def list_cases() -> list[dict[str, Any]]:
    root = case_root()
    if not root.is_dir():
        return []
    cases: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            cases.append(value)
    # Most actionable first: open cases before resolved, then by frame_count.
    cases.sort(
        key=lambda c: (
            c.get("status") in _RESOLVED_STATUSES,
            -int(c.get("frame_count") or 0),
        )
    )
    return cases


def _write_case(case: dict[str, Any]) -> Path:
    root = case_root()
    root.mkdir(parents=True, exist_ok=True)
    path = _case_path(str(case["case_id"]))
    path.write_text(
        json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def upsert_cluster(cluster: dict, *, source: str, now: str | None = None) -> dict[str, Any]:
    """Create or update the case for one cluster, applying lifecycle rules.

    - New cluster → create case in ``new``.
    - Existing case, frames still present → refresh frames; if it was in a
      resolved state (verified/released/closed) flip to ``reopened`` (the
      "问题发生两次就自动重开" rule); otherwise keep the working status.
    - Frame count and sample ids always reflect the latest run so the UI never
      shows stale evidence.

    Returns the persisted case dict."""
    ts = now or _now()
    case_id = str(cluster["case_id"])
    frame_ids = list(cluster.get("frame_ids", []))[:_MAX_STORED_FRAME_IDS]
    frame_count = int(cluster.get("frame_count") or len(frame_ids))
    existing = load_case(case_id)

    if existing is None:
        case = {
            "case_id": case_id,
            "dataset_key": cluster.get("dataset_key", ""),
            "failure_type": cluster.get("failure_type", ""),
            "title": cluster.get("title", case_id),
            "hint": cluster.get("hint", ""),
            "status": "new",
            "created_at": ts,
            "updated_at": ts,
            "frame_count": frame_count,
            "frame_ids": frame_ids,
            "first_seen": {"at": ts, "frame_count": frame_count, "source": source},
            "suspected_cause": "",
            "linked_fix": "",
            "history": [
                {
                    "at": ts,
                    "event": "opened",
                    "frame_count": frame_count,
                    "note": f"首次聚类（{source}）",
                }
            ],
        }
        _write_case(case)
        return case

    case = dict(existing)
    prev_status = case.get("status", "new")
    prev_count = int(case.get("frame_count") or 0)
    case["frame_ids"] = frame_ids
    case["frame_count"] = frame_count
    case["updated_at"] = ts
    # Preserve descriptive fields set by humans.
    case.setdefault("hint", cluster.get("hint", ""))
    history = list(case.get("history", []))

    # Auto-reopen semantics: a resolved case only reopens when the problem gets
    # WORSE than the count we accepted at resolution time (``resolved_frame_count``).
    # A small, stable residual that we already signed off on must NOT keep
    # reopening on every re-run — that would make "verified" meaningless. But a
    # genuine regression (more failing frames than we accepted) is exactly the
    # "问题又变严重了" signal we want surfaced.
    bar = existing.get("resolved_frame_count")
    if bar is None:
        bar = 0
    if prev_status in _RESOLVED_STATUSES and frame_count > int(bar):
        case["status"] = "reopened"
        history.append(
            {
                "at": ts,
                "event": "reopened",
                "frame_count": frame_count,
                "note": f"已判定{_status_label(prev_status)}（验收 {int(bar)} 帧）后回归到 {frame_count} 帧（{source}）",
            }
        )
    else:
        history.append(
            {
                "at": ts,
                "event": "recluster",
                "frame_count": frame_count,
                "note": f"重新聚类：{prev_count} → {frame_count}（{source}）",
            }
        )
    case["history"] = history
    _write_case(case)
    return case


def upsert_clusters(clusters: list[dict], *, source: str) -> list[dict[str, Any]]:
    ts = _now()
    return [upsert_cluster(c, source=source, now=ts) for c in clusters]


def set_status(case_id: str, status: str, *, note: str = "") -> dict[str, Any]:
    """Manually move a case along its lifecycle. Records the transition in
    history. Raises on unknown case or invalid status (no silent failure)."""
    if status not in CASE_STATUSES:
        raise ValueError(f"unknown case status: {status!r}")
    case = load_case(case_id)
    if case is None:
        raise ValueError(f"case not found: {case_id!r}")
    prev = case.get("status", "new")
    ts = _now()
    case["status"] = status
    case["updated_at"] = ts
    # Moving into a resolved state records the frame count we are accepting, so a
    # later re-cluster only reopens on a real regression above this bar.
    if status in _RESOLVED_STATUSES:
        case["resolved_frame_count"] = int(case.get("frame_count") or 0)
    history = list(case.get("history", []))
    history.append(
        {
            "at": ts,
            "event": "status",
            "from": prev,
            "to": status,
            "note": note,
        }
    )
    case["history"] = history
    _write_case(case)
    return case


def annotate(case_id: str, *, suspected_cause: str | None = None, linked_fix: str | None = None) -> dict[str, Any]:
    """Attach triage notes / a linked fix (commit or doc path) to a case."""
    case = load_case(case_id)
    if case is None:
        raise ValueError(f"case not found: {case_id!r}")
    ts = _now()
    changed = []
    if suspected_cause is not None:
        case["suspected_cause"] = suspected_cause
        changed.append("suspected_cause")
    if linked_fix is not None:
        case["linked_fix"] = linked_fix
        changed.append("linked_fix")
    if changed:
        case["updated_at"] = ts
        history = list(case.get("history", []))
        history.append({"at": ts, "event": "annotate", "fields": changed})
        case["history"] = history
        _write_case(case)
    return case


def status_label(status: str) -> str:
    return _status_label(status)


def _status_label(status: str) -> str:
    return {
        "new": "新建",
        "triaged": "已分诊",
        "fixing": "修复中",
        "verified": "已验证",
        "released": "已发布",
        "reopened": "已重开",
        "closed": "已关闭",
    }.get(status, status)


def failure_label(failure_type: str) -> str:
    return _FAILURE_LABEL.get(failure_type, failure_type)
