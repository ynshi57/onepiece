from app.frame_metadata import (
    build_frame_metadata_prompt,
    normalize_frame_quality,
    normalize_walking_roi,
    quality_gate_vqa_payload,
    should_short_circuit_quality,
)


def test_normalize_frame_quality_turns_blur_into_user_visible_hint():
    quality = normalize_frame_quality(
        {
            "blur": "blurry",
            "exposure": "ok",
            "occlusion": "ok",
            "confidence": "high",
            "usable_for_walking": True,
        }
    )

    assert quality["usable_for_walking"] is False
    assert quality["spoken_hint"] == "画面有些糊，请放慢。"
    assert should_short_circuit_quality(mode="walking", question="", frame_quality=quality)


def test_quality_gate_payload_is_cautious_not_low_risk():
    quality = normalize_frame_quality({"exposure": "too_dark", "confidence": "medium"})
    payload = quality_gate_vqa_payload(quality)

    assert payload["risk_level"] == "medium"
    assert "看不清" in payload["risk_message"]
    assert payload["distance_confidence"] == "none"
    assert payload["diagnostic_metrics"]["quality_gate"] == "short_circuit"


def test_explicit_question_does_not_short_circuit_quality_gate():
    quality = normalize_frame_quality({"occlusion": "covered", "confidence": "high"})

    assert not should_short_circuit_quality(mode="walking", question="这是什么？", frame_quality=quality)


def test_normalize_walking_roi_accepts_safe_normalized_rects():
    roi = normalize_walking_roi(
        {
            "coordinate_space": "normalized_image",
            "near_path": {"x": 0.2, "y": 0.45, "w": 0.6, "h": 0.55},
            "left_front": {"x": 0.0, "y": 0.4, "w": 0.35, "h": 0.6},
            "right_front": {"x": 0.65, "y": 0.4, "w": 0.35, "h": 0.6},
        }
    )

    assert roi["near_path"] == {"x": 0.2, "y": 0.45, "w": 0.6, "h": 0.55}
    assert roi["coordinate_space"] == "normalized_image"


def test_normalize_walking_roi_rejects_out_of_bounds_rect():
    assert normalize_walking_roi({"near_path": {"x": 0.8, "y": 0.8, "w": 0.4, "h": 0.4}}) is None


def test_frame_metadata_prompt_focuses_roi_without_hiding_side_risks():
    quality = normalize_frame_quality({})
    roi = normalize_walking_roi({"near_path": {"x": 0.2, "y": 0.45, "w": 0.6, "h": 0.55}})

    prompt = build_frame_metadata_prompt(mode="walking", frame_quality=quality, walking_roi=roi)

    assert "near_path ROI" in prompt
    assert "近处通行路径" in prompt
    assert "不要忽略 ROI 外" in prompt
    assert "车辆" in prompt


def test_quality_gate_applies_to_default_risk_observe_mode():
    quality = normalize_frame_quality({"blur": "blurry", "confidence": "high"})

    assert should_short_circuit_quality(mode="risk_observe", question="", frame_quality=quality)
    assert not should_short_circuit_quality(mode="", question="", frame_quality=quality)
