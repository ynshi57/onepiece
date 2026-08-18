from app.path_parity import compute_parity


def _row(frame_id, near, left, right, focus):
    return {
        "frame_id": frame_id,
        "prediction": {
            "near_path_status": near,
            "left_front_status": left,
            "right_front_status": right,
            "focus_direction": focus,
        },
    }


def test_identical_predictions_have_no_drift():
    ios = [_row("f1", "candidateOpen", "candidateOpen", "candidateOpen", "unknown")]
    server = [_row("f1", "candidateOpen", "candidateOpen", "candidateOpen", "unknown")]
    report = compute_parity(ios, server)
    assert report["shared_frames"] == 1
    assert report["overall_agreement"] == 1.0
    assert report["drift_rate"] == 0.0
    assert report["drift_alert"] is False


def test_divergent_predictions_raise_drift_alert():
    ios = [_row("f1", "blocked", "caution", "blocked", "center")]
    server = [_row("f1", "candidateOpen", "candidateOpen", "candidateOpen", "unknown")]
    report = compute_parity(ios, server, drift_threshold=0.2)
    assert report["shared_frames"] == 1
    # All four fields disagree.
    assert report["overall_agreement"] == 0.0
    assert report["drift_rate"] == 1.0
    assert report["drift_alert"] is True
    assert len(report["mismatches"]) == 4


def test_only_shared_frames_are_compared():
    ios = [_row("f1", "candidateOpen", "candidateOpen", "candidateOpen", "unknown")]
    server = [
        _row("f1", "candidateOpen", "candidateOpen", "candidateOpen", "unknown"),
        _row("f2", "blocked", "blocked", "blocked", "center"),
    ]
    report = compute_parity(ios, server)
    assert report["shared_frames"] == 1
    assert report["compared_fields"] == 4


def test_path_guidance_key_is_accepted_as_prediction():
    ios = [{"frame_id": "f1", "path_guidance": {"near_path_status": "caution"}}]
    server = [{"frame_id": "f1", "prediction": {"near_path_status": "caution"}}]
    report = compute_parity(ios, server)
    assert report["field_agreement"]["near_path_status"] == 1.0
