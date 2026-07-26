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


def test_max_token_ceilings_hold_a_valid_json():
    # Regression: the old 96/160 ceilings truncated every JSON reply
    # (finish_reason: length) so parsing failed on every frame. The ceilings must
    # comfortably exceed the token size of a minimal valid slim-schema object.
    # ~130 tokens is the observed size of a full Chinese slim JSON; require margin.
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
    assert set(rf["json_schema"]["schema"]["required"]) == {
        "objects",
        "scene",
        "description",
        "change_significance",
        "changes",
    }


def test_response_format_falls_back_to_json_object_for_ollama():
    # Ollama (:11434) doesn't support json_schema; must not send a payload it rejects.
    rf = vqa_service._build_response_format("http://127.0.0.1:11434")
    assert rf == {"type": "json_object"}


def test_truncated_json_surfaces_reason_not_person():
    # Simulate what the model emits when cut off mid-object (the real bug):
    # invalid JSON. It must NOT become a fabricated person.
    truncated = '{\n  "objects": ["行人"],\n  "scene": "街道",\n  "summar'
    result = vqa_service._parse_qwen_content(truncated, fallback_prompt="p")
    assert "person" not in result["objects"]
    assert result["objects"] == []
    assert "模型未按要求输出" in result["description"]
