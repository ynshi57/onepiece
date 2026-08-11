"""Tests for vqa_service model-output parsing and fallback behavior.

Focus: when the model does NOT return the required JSON (the common case with a
direct llama-server that doesn't enforce response_format), the service must
surface the model's real text / a specific reason instead of fabricating a
"画面中可能有人" person the model never reported. (CLAUDE.md: No Silent Failures.)
"""

from app import vqa_service
from app.fusion import fuse_vqa_result


def test_parse_valid_json_is_normalized():
    content = (
        '{"objects": ["door"], "scene": "hallway", "description": "正前方是一条走廊。",'
        ' "risk_level": "low"}'
    )
    result = vqa_service._parse_qwen_content(content, fallback_prompt="p")
    assert result["objects"] == ["door"]
    assert result["scene"] == "hallway"
    assert result["description"] == "正前方是一条走廊。"


def test_parse_non_json_does_not_fabricate_person():
    # Model emitted a plain-text Markdown description (no JSON) that has nothing
    # to do with people. The old code injected objects:["person"] + "有人".
    plain_text = "这是一间明亮的厨房，可以看到水槽和窗户。"
    result = vqa_service._parse_qwen_content(plain_text, fallback_prompt="p")

    assert result["objects"] == [], "must not fabricate objects the model never reported"
    assert "有人" not in result.get("summary", "")
    assert "person" not in result["objects"]
    # The real model text is preserved so downstream fusion can use it.
    assert plain_text in result["description"]


def test_parse_non_json_surfaces_specific_reason():
    result = vqa_service._parse_qwen_content("随便一段非JSON文本", fallback_prompt="p")
    # A specific diagnostic reason is shown rather than a silent generic guess.
    assert "模型未按要求输出" in result["description"]


def test_parse_non_json_fuses_into_real_summary_not_person():
    # End-to-end: a non-JSON model reply must produce a fused result whose
    # summary reflects the model's real text, never the fabricated person.
    plain_text = "前方是一段向下的楼梯，请注意台阶。"
    parsed = vqa_service._parse_qwen_content(plain_text, fallback_prompt="p")
    fused = fuse_vqa_result(parsed, gps_payload=None, latency_ms=1.0)

    assert fused["objects"] == []
    assert "画面中可能有人" not in fused["summary"]
    assert "画面中可能有人" not in fused["spoken_text"]
    # fusion derives summary from the real description text.
    assert plain_text in fused["summary"]
    # "楼梯"/"台阶" are high-risk keywords -> risk should escalate from the text.
    assert fused["risk_level"] == "high"


def test_empty_content_falls_back_without_person():
    result = vqa_service._parse_qwen_content("", fallback_prompt="p")
    assert result["objects"] == []
    assert "有人" not in result.get("summary", "")
    assert "无法识别" in result["summary"]


def test_heuristic_generic_branch_no_longer_claims_person():
    # The generic heuristic (no road keywords) must not assert a person exists.
    result = vqa_service._heuristic_vqa(prompt="随便")
    assert "person" not in result["objects"]
    assert "有人" not in result["summary"]
    assert "无法识别" in result["summary"]


def test_heuristic_road_branch_unchanged():
    # The road heuristic is a legitimate keyword-driven hint and stays intact.
    result = vqa_service._heuristic_vqa(prompt="前方道路")
    assert "car" in result["objects"]
    assert result["risk_level"] == "medium"


def test_max_token_ceilings_hold_valid_json():
    # Regression: the old 96/160 ceilings truncated every JSON reply
    # (finish_reason: length). Fast walking/surroundings replies can be shorter,
    # but still need enough room for valid Chinese JSON.
    assert vqa_service._MAX_TOKENS_FAST >= 180
    assert vqa_service._MAX_TOKENS_INCREMENTAL >= 180
    assert vqa_service._MAX_TOKENS_FULL >= vqa_service._MAX_TOKENS_INCREMENTAL


def test_response_format_uses_json_schema_for_llama_server():
    # Direct llama-server (:11435) must get grammar-enforced json_schema so the
    # mode prose prompts can't make it emit Markdown instead of JSON.
    rf = vqa_service._build_response_format("http://127.0.0.1:11435")
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    props = rf["json_schema"]["schema"]["properties"]
    assert props["change_significance"]["enum"] == ["none", "minor", "major"]
    assert props["risk_zone"]["enum"] == ["immediate", "near", "mid", "far", "unknown"]
    assert "distance_m" not in props
    assert set(rf["json_schema"]["schema"]["required"]) == {
        "objects",
        "scene",
        "description",
        "summary",
        "spatial_description",
        "risk_level",
        "risk_message",
        "suggested_action",
        "spoken_text",
        "ocr_text",
        "change_significance",
        "changes",
    }



def test_fast_response_format_uses_compact_safety_schema_for_llama_server():
    rf = vqa_service._build_response_format("http://127.0.0.1:11435", fast_response=True)

    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "vqa_fast_result"
    required = set(rf["json_schema"]["schema"]["required"])
    assert {
        "objects",
        "scene",
        "summary",
        "spatial_description",
        "risk_level",
        "risk_message",
        "suggested_action",
        "spoken_text",
        "change_significance",
        "changes",
    } == required
    assert "description" not in required
    assert "ocr_text" not in required
    assert "risk_zone" not in required


def test_walking_fast_response_format_uses_near_path_schema():
    rf = vqa_service._build_response_format(
        "http://127.0.0.1:11435",
        fast_response=True,
        walking_fast_response=True,
    )

    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "vqa_walking_fast_result"
    required = set(rf["json_schema"]["schema"]["required"])
    assert {"risk_zone", "direction", "distance_confidence"}.issubset(required)
    props = rf["json_schema"]["schema"]["properties"]
    assert "distance_m" not in props
    assert "estimated_distance" not in props
    assert "meters" not in props

def test_response_format_falls_back_to_json_object_for_ollama():
    # Ollama (:11434) doesn't support json_schema; must not send a payload it rejects.
    rf = vqa_service._build_response_format("http://127.0.0.1:11434")
    assert rf == {"type": "json_object"}


def test_direct_llama_runtime_ignores_model_override(monkeypatch):
    monkeypatch.setenv("QWEN_MODEL", "qwen2.5vl:3b")
    info = vqa_service._resolve_qwen_model_info(
        "http://127.0.0.1:11435",
        model_override="qwen2.5vl:7b",
    )

    assert info["dynamic_model_selection"] is False
    assert info["requested_model"] == "qwen2.5vl:7b"
    assert info["resolved_model"] == "qwen2.5vl:3b"
    assert info["routing_reason"] == "single_runtime_ignored_override"


def test_ollama_runtime_allows_model_override(monkeypatch):
    monkeypatch.setenv("QWEN_MODEL", "qwen2.5vl:3b")
    info = vqa_service._resolve_qwen_model_info(
        "http://127.0.0.1:11434",
        model_override="qwen2.5vl:7b",
    )

    assert info["dynamic_model_selection"] is True
    assert info["resolved_model"] == "qwen2.5vl:7b"
    assert info["routing_reason"] == "override"


def test_runtime_status_for_direct_llama_runtime(monkeypatch):
    monkeypatch.setenv("QWEN_API_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("QWEN_MODEL", "qwen2.5vl:3b")
    status = vqa_service.runtime_status()

    assert status["status"] == "qwen"
    assert status["dynamic_model_selection"] is False
    assert status["available_models"] == ["qwen2.5vl:3b"]
    assert status["resolved_model"] == "qwen2.5vl:3b"


def test_truncated_json_surfaces_reason_not_person():
    # Simulate what the model emits when cut off mid-object (the real bug):
    # invalid JSON. It must NOT become a fabricated person.
    truncated = '{\n  "objects": ["行人"],\n  "scene": "街道",\n  "summar'
    result = vqa_service._parse_qwen_content(truncated, fallback_prompt="p")
    assert "person" not in result["objects"]
    assert result["objects"] == []
    assert "模型未按要求输出" in result["description"]


def test_broken_json_does_not_surface_raw_json_as_user_summary():
    broken = '{"objects":["床","衣物","衣物","衣物","衣物"'
    result = vqa_service._parse_qwen_content(broken, fallback_prompt="p")
    fused = fuse_vqa_result(result, gps_payload=None, latency_ms=1.0)

    assert "objects" not in fused["summary"]
    assert "衣物" not in fused["summary"]
    assert "模型输出异常" in fused["summary"]
    assert "模型输出异常" in fused["spoken_text"]


def test_incremental_fast_request_does_not_send_previous_image_by_default(monkeypatch):
    monkeypatch.setenv("QWEN_API_BASE_URL", "http://127.0.0.1:11435")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"objects":[],"scene":"走廊","summary":"无明显变化。","spatial_description":"左侧信息不足，正前方可通行，右侧信息不足。","risk_level":"low","risk_message":"暂未发现明显危险。","suggested_action":"继续缓慢前进。","spoken_text":"前方可通行。","risk_zone":"near","direction":"front","distance_confidence":"low","change_significance":"none","changes":"无明显变化"}'
                        }
                    }
                ]
            }

    def fake_post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(vqa_service.httpx, "post", fake_post)
    result = vqa_service.run_vqa_from_frame(
        prompt="模式=行走。",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        incremental=True,
        previous_image_base64="not-valid-base64-because-default-should-ignore-it",
        fast_response=True,
    )

    images = [
        part
        for part in captured["payload"]["messages"][1]["content"]
        if part.get("type") == "image_url"
    ]
    assert len(images) == 1
    assert captured["payload"]["max_tokens"] == vqa_service._MAX_TOKENS_FAST
    assert captured["payload"]["response_format"]["json_schema"]["name"] == "vqa_walking_fast_result"
    assert result["change_significance"] == "none"


def test_incremental_can_opt_in_to_previous_image_validation(monkeypatch):
    monkeypatch.setenv("QWEN_SEND_PREVIOUS_IMAGE_IN_INCREMENTAL", "1")

    try:
        vqa_service.run_vqa_from_frame(
            prompt="模式=行走。",
            image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            incremental=True,
            previous_image_base64="not-valid-base64",
        )
    except Exception as exc:
        assert "base64" in type(exc).__name__.lower() or "base64" in str(exc).lower()
    else:
        raise AssertionError("invalid opt-in previous image must fail visibly")


def test_parse_qwen_content_preserves_near_path_fields():
    content = (
        '{"objects":["台阶"],"scene":"人行道","description":"近处正前方疑似有台阶。",'
        '"risk_zone":"near","direction":"front","distance_confidence":"low"}'
    )

    result = vqa_service._parse_qwen_content(content, fallback_prompt="p")

    assert result["risk_zone"] == "near"
    assert result["direction"] == "front"
    assert result["distance_confidence"] == "low"


def test_parse_qwen_content_rejects_fake_distance_enums():
    content = (
        '{"objects":[],"scene":"人行道","description":"前方可通行。",'
        '"risk_zone":"3m","direction":"straight_ahead","distance_confidence":"certain"}'
    )

    result = vqa_service._parse_qwen_content(content, fallback_prompt="p")

    assert "risk_zone" not in result
    assert "direction" not in result
    assert "distance_confidence" not in result


def test_fast_schema_exposes_risk_zone_not_meter_distance():
    rf = vqa_service._build_response_format(
        "http://127.0.0.1:11435",
        fast_response=True,
        walking_fast_response=True,
    )
    schema = rf["json_schema"]["schema"]

    assert "risk_zone" in schema["required"]
    assert "direction" in schema["required"]
    assert "distance_confidence" in schema["required"]
    assert "distance_m" not in schema["properties"]
    assert "estimated_distance" not in schema["properties"]
    assert "meters" not in schema["properties"]


def test_risk_observe_fast_request_uses_near_path_schema(monkeypatch):
    monkeypatch.setenv("QWEN_API_BASE_URL", "http://127.0.0.1:11435")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"objects":[],"scene":"办公室","summary":"近处通行路径暂未发现明显风险。","spatial_description":"左侧信息不足，正前方可通行，右侧信息不足。","risk_level":"low","risk_message":"暂未发现明显危险。","suggested_action":"继续观察，缓慢移动。","spoken_text":"近处暂未发现明显风险。","risk_zone":"near","direction":"front","distance_confidence":"low","change_significance":"major","changes":""}'
                        }
                    }
                ]
            }

    def fake_post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(vqa_service.httpx, "post", fake_post)
    result = vqa_service.run_vqa_from_frame(
        prompt="模式=风险观察。",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        fast_response=True,
    )

    assert captured["payload"]["response_format"]["json_schema"]["name"] == "vqa_walking_fast_result"
    assert result["risk_zone"] == "near"
    assert result["diagnostic_metrics"]["walking_fast_response"] is True
