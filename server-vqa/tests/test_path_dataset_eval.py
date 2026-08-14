from app.path_dataset_eval import evaluate_path_guidance


def test_evaluate_path_guidance_finds_risk_miss():
    rows = [
        {
            "frame_id": "frame-1",
            "split": "indoor",
            "ground_truth": {
                "near_path_status": "blocked",
                "left_front_status": "candidateOpen",
                "right_front_status": "candidateOpen",
                "focus_direction": "center",
            },
            "prediction": {
                "near_path_status": "candidateOpen",
                "left_front_status": "candidateOpen",
                "right_front_status": "candidateOpen",
                "focus_direction": "center",
            },
        }
    ]

    report = evaluate_path_guidance(rows)

    assert report["labeled_frames"] == 1
    assert report["risk_miss_count"] == 1
    assert report["risk_misses"] == ["frame-1:near_path_status"]
    assert report["status_accuracy"] == 0.6667


def test_evaluate_path_guidance_external_predictions_override_manifest():
    rows = [
        {
            "frame_id": "frame-1",
            "ground_truth": {
                "near_path_status": "caution",
                "left_front_status": "candidateOpen",
                "right_front_status": "candidateOpen",
                "focus_direction": "right",
            },
            "prediction": {
                "near_path_status": "candidateOpen",
                "left_front_status": "candidateOpen",
                "right_front_status": "candidateOpen",
                "focus_direction": "unknown",
            },
        }
    ]
    predictions = [
        {
            "frame_id": "frame-1",
            "path_guidance": {
                "near_path_status": "caution",
                "left_front_status": "candidateOpen",
                "right_front_status": "candidateOpen",
                "focus_direction": "right",
            },
        }
    ]

    report = evaluate_path_guidance(rows, predictions)

    assert report["risk_miss_count"] == 0
    assert report["status_accuracy"] == 1.0
    assert report["focus_direction_accuracy"] == 1.0
