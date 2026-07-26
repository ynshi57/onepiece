from app.fusion import fuse_vqa_result


def test_fuse_vqa_result_combines_scene_objects_and_location():
    vision_payload = {
        "objects": ["car", "traffic_light"],
        "scene": "city street",
        "vision_location": "outdoor road",
    }
    gps_payload = {"lat": 39.9042, "lon": 116.4074}

    result = fuse_vqa_result(vision_payload=vision_payload, gps_payload=gps_payload)

    assert result["objects"] == ["car", "traffic_light"]
    assert result["scene"] == "city street"
    assert result["vision_location"] == "outdoor road"
    assert result["gps_location"] == {"lat": 39.9042, "lon": 116.4074}
    assert result["summary"]
    assert result["spatial_description"]
    assert result["risk_level"] in {"low", "medium", "high"}
    assert result["risk_message"]
    assert result["suggested_action"]
    assert result["spoken_text"]
    # No change_significance from the model -> default to "major" so the client speaks.
    assert result["change_significance"] == "major"
    assert result["changes"] == ""
    assert "timestamp" in result


def test_fuse_vqa_result_falls_back_when_vision_fields_missing():
    vision_payload = {}
    gps_payload = {"lat": 0.0, "lon": 0.0}

    result = fuse_vqa_result(vision_payload=vision_payload, gps_payload=gps_payload)

    assert result["objects"] == []
    assert result["scene"] == "unknown"
    assert result["vision_location"] == "unknown"
    assert result["summary"] == "暂时无法可靠判断画面内容。"
    assert result["spatial_description"]
    assert result["risk_level"] == "low"


def test_fuse_vqa_result_prefers_model_assistance_fields():
    vision_payload = {
        "objects": ["door"],
        "scene": "hallway",
        "vision_location": "indoor",
        "description": "室内走廊。",
        "summary": "正前方是一条走廊。",
        "spatial_description": "左侧是墙，正前方是走廊，右侧可能有门。",
        "risk_level": "medium",
        "risk_message": "右侧门附近可能有人经过。",
        "suggested_action": "请靠左慢行。",
        "spoken_text": "正前方是一条走廊，请靠左慢行。",
        "ocr_text": "EXIT",
    }

    result = fuse_vqa_result(vision_payload=vision_payload, gps_payload=None)

    assert result["summary"] == "正前方是一条走廊。"
    assert result["spatial_description"] == "左侧是墙，正前方是走廊，右侧可能有门。"
    assert result["risk_level"] == "medium"
    assert result["risk_message"] == "右侧门附近可能有人经过。"
    assert result["suggested_action"] == "请靠左慢行。"
    assert result["spoken_text"] == "正前方是一条走廊，请靠左慢行。"
    assert result["ocr_text"] == "EXIT"


def test_fuse_vqa_result_passes_through_change_fields():
    vision_payload = {
        "objects": ["door"],
        "scene": "hallway",
        "vision_location": "indoor",
        "description": "无明显变化。",
        "change_significance": "None",  # case-insensitive normalization
        "changes": "无明显变化",
    }

    result = fuse_vqa_result(vision_payload=vision_payload, gps_payload=None)

    assert result["change_significance"] == "none"
    assert result["changes"] == "无明显变化"


def test_fuse_vqa_result_defaults_invalid_change_significance_to_major():
    vision_payload = {
        "objects": ["car"],
        "scene": "street",
        "vision_location": "outdoor",
        "change_significance": "totally-not-valid",
    }

    result = fuse_vqa_result(vision_payload=vision_payload, gps_payload=None)

    assert result["change_significance"] == "major"
