from app.eval_baseline import load_baseline, metrics_from_report, save_baseline
from app.regression_gate import GateThresholds, check_regression


def _report(**overrides):
    base = {
        "frame_count": 10,
        "labeled_frames": 10,
        "status_accuracy": 0.9,
        "focus_direction_accuracy": 0.9,
        "unknown_prediction_rate": 0.1,
        "risk_miss_count": 0,
        "false_block_count": 1,
        "missing_prediction_count": 0,
    }
    base.update(overrides)
    return base


def test_baseline_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(tmp_path))
    path = save_baseline("camvid-road", _report(), source="manifest:camvid.jsonl")
    assert path.is_file()
    loaded = load_baseline("camvid-road")
    assert loaded is not None
    assert loaded["source"] == "manifest:camvid.jsonl"
    assert loaded["sample_count"] == 10
    assert loaded["metrics"]["status_accuracy"] == 0.9


def test_baseline_stores_only_tracked_metrics():
    metrics = metrics_from_report(_report(extra_noise="should_not_persist"))
    assert "extra_noise" not in metrics
    assert set(metrics) == {
        "frame_count",
        "labeled_frames",
        "status_accuracy",
        "focus_direction_accuracy",
        "unknown_prediction_rate",
        "risk_miss_count",
        "false_block_count",
        "missing_prediction_count",
    }


def test_gate_passes_when_metrics_hold():
    baseline = {"metrics": metrics_from_report(_report())}
    current = _report()
    result = check_regression(current, baseline)
    assert result.passed is True
    assert result.violations == []


def test_gate_fails_on_new_risk_miss():
    baseline = {"metrics": metrics_from_report(_report(risk_miss_count=0))}
    current = _report(risk_miss_count=2)
    result = check_regression(current, baseline)
    assert result.passed is False
    assert any("risk_miss_count" in violation for violation in result.violations)


def test_gate_fails_on_accuracy_drop_beyond_epsilon():
    baseline = {"metrics": metrics_from_report(_report(status_accuracy=0.9))}
    current = _report(status_accuracy=0.8)  # dropped 0.1 > default 0.02
    result = check_regression(current, baseline)
    assert result.passed is False
    assert any("status_accuracy" in violation for violation in result.violations)


def test_gate_tolerates_small_accuracy_drop():
    baseline = {"metrics": metrics_from_report(_report(status_accuracy=0.9))}
    current = _report(status_accuracy=0.89)  # dropped 0.01 <= 0.02
    result = check_regression(current, baseline)
    assert result.passed is True


def test_gate_thresholds_are_configurable():
    baseline = {"metrics": metrics_from_report(_report(risk_miss_count=0))}
    current = _report(risk_miss_count=1)
    strict = check_regression(current, baseline, GateThresholds(max_risk_miss_increase=0))
    lenient = check_regression(current, baseline, GateThresholds(max_risk_miss_increase=1))
    assert strict.passed is False
    assert lenient.passed is True
