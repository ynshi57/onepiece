from app.path_parity import compute_parity


def _row(frame_id, cells):
    return {
        "frame_id": frame_id,
        "prediction": {
            "traversable_grid": {"cols": 2, "rows": 2, "cells": cells},
        },
    }


def test_identical_predictions_have_no_drift():
    ios = [_row("f1", [1, 0, 0, 1])]
    server = [_row("f1", [1, 0, 0, 1])]
    report = compute_parity(ios, server)
    assert report["shared_frames"] == 1
    assert report["overall_agreement"] == 1.0
    assert report["drift_rate"] == 0.0
    assert report["drift_alert"] is False


def test_divergent_predictions_raise_drift_alert():
    ios = [_row("f1", [1, 1, 1, 1])]
    server = [_row("f1", [0, 0, 0, 0])]
    report = compute_parity(ios, server, drift_threshold=0.2)
    assert report["shared_frames"] == 1
    assert report["overall_agreement"] == 0.0
    assert report["drift_rate"] == 1.0
    assert report["drift_alert"] is True
    assert len(report["mismatches"]) == 1


def test_only_shared_frames_are_compared():
    ios = [_row("f1", [1, 0, 0, 0])]
    server = [
        _row("f1", [1, 0, 0, 0]),
        _row("f2", [0, 0, 0, 1]),
    ]
    report = compute_parity(ios, server)
    assert report["shared_frames"] == 1
    assert report["compared_fields"] == 1


def test_path_guidance_key_is_accepted_as_prediction():
    ios = [{"frame_id": "f1", "path_guidance": {"traversable_grid": {"cols": 1, "rows": 1, "cells": [1]}}}]
    server = [{"frame_id": "f1", "prediction": {"traversable_grid": {"cols": 1, "rows": 1, "cells": [1]}}}]
    report = compute_parity(ios, server)
    assert report["field_agreement"]["traversable_grid"] == 1.0
