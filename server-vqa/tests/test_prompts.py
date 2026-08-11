import json

import pytest

from app import prompts
from app.prompts import DEFAULT_MODE, get_templates, resolve_prompt


def test_resolve_prompt_uses_mode_template():
    result = resolve_prompt(mode="walking")
    assert "行走" in result
    assert result == get_templates()["walking"]


def test_resolve_prompt_falls_back_to_legacy_prompt_for_unknown_mode():
    result = resolve_prompt(mode="does_not_exist", legacy_prompt="旧客户端的完整提示")
    assert result == "旧客户端的完整提示"


def test_resolve_prompt_defaults_when_nothing_provided():
    result = resolve_prompt()
    assert result == get_templates()[DEFAULT_MODE]


def test_resolve_prompt_appends_user_question():
    result = resolve_prompt(mode="detail", question="这瓶饮料是什么口味？")
    assert get_templates()["detail"] in result
    assert "这瓶饮料是什么口味？" in result
    assert "请优先、直接回答这个问题" in result


def test_resolve_prompt_question_works_without_mode():
    result = resolve_prompt(question="前面是红灯还是绿灯？")
    # base falls back to default mode, but the question must still be present
    assert "前面是红灯还是绿灯？" in result


def test_resolve_prompt_strips_whitespace():
    assert resolve_prompt(mode="  walking  ") == get_templates()["walking"]
    assert resolve_prompt(question="   ") == get_templates()[DEFAULT_MODE]


def test_get_templates_applies_json_override(tmp_path, monkeypatch):
    override_file = tmp_path / "prompts.json"
    override_file.write_text(
        json.dumps({"walking": "自定义行走提示", "surroundings": "  "}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VQA_PROMPTS_FILE", str(override_file))

    templates = get_templates()
    # non-empty override replaces the builtin
    assert templates["walking"] == "自定义行走提示"
    # blank override is ignored; builtin remains
    assert templates["surroundings"] == prompts._BUILTIN_TEMPLATES["surroundings"]


def test_get_templates_ignores_missing_override_file(monkeypatch):
    monkeypatch.setenv("VQA_PROMPTS_FILE", "/nonexistent/path/prompts.json")
    templates = get_templates()
    assert templates == prompts._BUILTIN_TEMPLATES


def test_get_templates_ignores_malformed_override_file(tmp_path, monkeypatch):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not valid json {{{", encoding="utf-8")
    monkeypatch.setenv("VQA_PROMPTS_FILE", str(bad_file))

    templates = get_templates()
    assert templates == prompts._BUILTIN_TEMPLATES


def test_walking_prompt_focuses_near_path_without_fake_meters():
    result = resolve_prompt(mode="walking")

    assert "近处通行路径" in result
    assert "画面下半部" in result
    assert "不要估算具体米数" in result
    assert "3米" not in result
    assert "3 米" not in result
    assert "米内" not in result


def test_default_prompt_is_unified_risk_observe():
    result = resolve_prompt()

    assert DEFAULT_MODE == "risk_observe"
    assert result == get_templates()["risk_observe"]
    assert "模式=风险观察" in result
    assert "障碍" in result
    assert "台阶" in result
    assert "车辆" in result
    assert "不要估算具体米数" in result
    assert "不要说可以走" in result
    assert "可以开" in result
    assert "安全通过" in result
