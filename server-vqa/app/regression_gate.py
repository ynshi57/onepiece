"""Regression gate for VQASee path-guidance coverage.

Region IoU and guidance-line quality are gated elsewhere (``region_grid`` /
harness eval). This helper only flags missing predictions against a saved
coverage baseline. Three-zone ROI accuracies are not a product signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GateThresholds:
    max_missing_prediction_increase: int = 0


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

    cur = _num(current.get("missing_prediction_count"))
    bas = _num(base.get("missing_prediction_count"))
    if cur is None or bas is None:
        record(
            "missing_prediction_count",
            True,
            "skipped (missing value)",
            current.get("missing_prediction_count"),
            base.get("missing_prediction_count"),
        )
    else:
        increase = int(cur - bas)
        ok = increase <= thresholds.max_missing_prediction_increase
        record(
            "missing_prediction_count",
            ok,
            f"increased {increase} (allowed {thresholds.max_missing_prediction_increase})",
            int(cur),
            int(bas),
        )

    return GateResult(passed=not violations, violations=violations, checks=checks)
