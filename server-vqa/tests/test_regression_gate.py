from app.eval_baseline import load_baseline, metrics_from_report, save_baseline
from app.regression_gate import GateThresholds, check_regression


def _report(**overrides):
    base = {
        "frame_count": 10,
        "labeled_frames": 10,
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
    assert loaded["metrics"]["missing_prediction_count"] == 0
    assert "status_accuracy" not in loaded["metrics"]


def test_baseline_stores_only_tracked_metrics():
    metrics = metrics_from_report(_report(extra_noise="should_not_persist", status_accuracy=0.9))
    assert "extra_noise" not in metrics
    assert "status_accuracy" not in metrics
    assert set(metrics) == {
        "frame_count",
        "labeled_frames",
        "missing_prediction_count",
    }


def test_gate_passes_when_metrics_hold():
    baseline = {"metrics": metrics_from_report(_report())}
    current = _report()
    result = check_regression(current, baseline)
    assert result.passed is True
    assert result.violations == []


def test_gate_fails_on_new_missing_predictions():
    baseline = {"metrics": metrics_from_report(_report(missing_prediction_count=0))}
    current = _report(missing_prediction_count=2)
    result = check_regression(current, baseline)
    assert result.passed is False
    assert any("missing_prediction_count" in violation for violation in result.violations)


def test_gate_thresholds_are_configurable():
    baseline = {"metrics": metrics_from_report(_report(missing_prediction_count=0))}
    current = _report(missing_prediction_count=1)
    strict = check_regression(current, baseline, GateThresholds(max_missing_prediction_increase=0))
    lenient = check_regression(current, baseline, GateThresholds(max_missing_prediction_increase=1))
    assert strict.passed is False
    assert lenient.passed is True
