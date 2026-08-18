"""Regression gate for VQASee path-guidance quality.

Compares a current evaluation report against a saved baseline and decides
whether quality regressed. The point is to make release/merge decisions
reproducible evidence instead of subjective memory.

Default policy is safety-first, matching VQASee's non-negotiables:
- ``risk_miss_count`` must not increase (missing a real risk is the worst
  failure), and by default has zero tolerance.
- ``status_accuracy`` / ``focus_direction_accuracy`` must not drop more than a
  small epsilon.
- ``unknown_prediction_rate`` and ``false_block_count`` must not worsen beyond
  their tolerances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GateThresholds:
    # Accuracy metrics may drop by at most this much vs baseline.
    max_status_accuracy_drop: float = 0.02
    max_direction_accuracy_drop: float = 0.02
    # Count metrics may increase by at most this many vs baseline.
    max_risk_miss_increase: int = 0
    max_false_block_increase: int = 0
    # Rate metric may increase by at most this much vs baseline.
    max_unknown_rate_increase: float = 0.05


@dataclass
class GateResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "violations": self.violations, "checks": self.checks}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _baseline_metrics(baseline: dict[str, Any]) -> dict[str, Any]:
    metrics = baseline.get("metrics")
    if isinstance(metrics, dict):
        return metrics
    return baseline


def check_regression(
    current: dict[str, Any],
    baseline: dict[str, Any],
    thresholds: GateThresholds | None = None,
) -> GateResult:
    thresholds = thresholds or GateThresholds()
    base = _baseline_metrics(baseline)
    violations: list[str] = []
    checks: list[dict[str, Any]] = []

    def record(metric: str, ok: bool, detail: str, current_value: Any, baseline_value: Any) -> None:
        checks.append(
            {
                "metric": metric,
                "ok": ok,
                "current": current_value,
                "baseline": baseline_value,
                "detail": detail,
            }
        )
        if not ok:
            violations.append(f"{metric}: {detail}")

    # Accuracy drops (higher is better).
    for metric, max_drop in (
        ("status_accuracy", thresholds.max_status_accuracy_drop),
        ("focus_direction_accuracy", thresholds.max_direction_accuracy_drop),
    ):
        cur = _num(current.get(metric))
        bas = _num(base.get(metric))
        if cur is None or bas is None:
            record(metric, True, "skipped (missing value)", current.get(metric), base.get(metric))
            continue
        drop = round(bas - cur, 6)
        ok = drop <= max_drop
        record(metric, ok, f"dropped {drop} (allowed {max_drop})", cur, bas)

    # Count increases (lower is better).
    for metric, max_increase in (
        ("risk_miss_count", thresholds.max_risk_miss_increase),
        ("false_block_count", thresholds.max_false_block_increase),
    ):
        cur = _num(current.get(metric))
        bas = _num(base.get(metric))
        if cur is None or bas is None:
            record(metric, True, "skipped (missing value)", current.get(metric), base.get(metric))
            continue
        increase = int(cur - bas)
        ok = increase <= max_increase
        record(metric, ok, f"increased {increase} (allowed {max_increase})", int(cur), int(bas))

    # Rate increase (lower is better).
    cur_unknown = _num(current.get("unknown_prediction_rate"))
    bas_unknown = _num(base.get("unknown_prediction_rate"))
    if cur_unknown is None or bas_unknown is None:
        record("unknown_prediction_rate", True, "skipped (missing value)", current.get("unknown_prediction_rate"), base.get("unknown_prediction_rate"))
    else:
        increase = round(cur_unknown - bas_unknown, 6)
        ok = increase <= thresholds.max_unknown_rate_increase
        record("unknown_prediction_rate", ok, f"increased {increase} (allowed {thresholds.max_unknown_rate_increase})", cur_unknown, bas_unknown)

    return GateResult(passed=not violations, violations=violations, checks=checks)
