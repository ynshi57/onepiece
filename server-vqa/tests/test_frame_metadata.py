from app.frame_metadata import (
    build_frame_metadata_prompt,
    normalize_frame_quality,
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


def test_frame_metadata_prompt_keeps_quality_and_drops_roi_boxes():
    quality = normalize_frame_quality({"blur": "ok", "confidence": "low"})
    prompt = build_frame_metadata_prompt(mode="walking", frame_quality=quality)

    assert "图像质量提示" in prompt
    assert "blur=ok" in prompt
    assert "near_path ROI" not in prompt
    assert "left_front ROI" not in prompt
    assert "right_front ROI" not in prompt
    assert "不要忽略 ROI 外" not in prompt


def test_quality_gate_applies_to_default_risk_observe_mode():
    quality = normalize_frame_quality({"blur": "blurry", "confidence": "high"})

    assert should_short_circuit_quality(mode="risk_observe", question="", frame_quality=quality)
    assert not should_short_circuit_quality(mode="", question="", frame_quality=quality)
