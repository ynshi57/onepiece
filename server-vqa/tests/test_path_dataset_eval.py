from app.path_dataset_eval import evaluate_path_guidance


def test_evaluate_path_guidance_counts_grid_labels():
    rows = [
        {
            "frame_id": "frame-1",
            "split": "indoor",
            "traversable_grid": {"cols": 2, "rows": 2, "cells": [1, 0, 0, 1]},
            "prediction": {
                "prediction_source": "ios_coreml_offline_harness",
                "traversable_grid": {"cols": 2, "rows": 2, "cells": [1, 0, 0, 1]},
            },
        }
    ]

    report = evaluate_path_guidance(rows)

    assert report["labeled_frames"] == 1
    assert report["missing_prediction_count"] == 0
    assert "status_accuracy" not in report
    assert "focus_direction_accuracy" not in report


def test_evaluate_path_guidance_external_predictions_override_manifest():
    rows = [
        {
            "frame_id": "frame-1",
            "traversable_grid": {"cols": 2, "rows": 2, "cells": [1, 0, 0, 0]},
        }
    ]
    predictions = [
        {
            "frame_id": "frame-1",
            "path_guidance": {
                "traversable_grid": {"cols": 2, "rows": 2, "cells": [0, 0, 0, 0]},
            },
        }
    ]

    report = evaluate_path_guidance(rows, predictions)

    assert report["labeled_frames"] == 1
    assert report["missing_prediction_count"] == 0


def test_evaluate_path_guidance_records_missing_predictions():
    rows = [
        {
            "frame_id": "frame-1",
            "traversable_grid": {"cols": 2, "rows": 2, "cells": [1, 0, 0, 0]},
        }
    ]

    report = evaluate_path_guidance(rows)

    assert report["missing_prediction_count"] == 1
    assert report["missing_predictions"] == ["frame-1"]
