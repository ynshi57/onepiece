from app.scene_context import build_contextual_prompt


BASE = "模式=周围。用中文描述整体场景和空间布局。"


def test_no_context_returns_base_prompt_unchanged():
    assert build_contextual_prompt(BASE, mode="surroundings", context=None) == BASE


def test_empty_context_dict_returns_base_prompt_unchanged():
    # Older/edge clients may send an empty object; treat as no continuity.
    assert build_contextual_prompt(BASE, mode="surroundings", context={}) == BASE


def test_context_appends_continuity_block_and_change_instruction():
    prompt = build_contextual_prompt(
        BASE,
        mode="surroundings",
        context={
            "prev_summary": "正前方是一条走廊，右侧有一扇门。",
            "prev_scene": "hallway",
            "prev_objects": ["door", "person"],
            "place_label": "中关村南路附近",
            "elapsed_ms": 2000,
        },
    )

    assert prompt.startswith(BASE)
    assert "【连续观察上下文】" in prompt
    assert "中关村南路附近" in prompt
    assert "正前方是一条走廊" in prompt
    assert "door" in prompt and "person" in prompt
    # The core behaviour: instruct the model to report only important changes.
    assert "change_significance" in prompt
    assert "重要变化" in prompt


def test_prev_objects_are_capped():
    many = [f"obj{i}" for i in range(20)]
    prompt = build_contextual_prompt(
        BASE, mode="surroundings", context={"prev_objects": many}
    )
    # Only the first _MAX_PREV_OBJECTS (8) are echoed to bound token growth.
    assert "obj0" in prompt
    assert "obj7" in prompt
    assert "obj8" not in prompt


def test_elapsed_phrase_uses_minutes_for_long_gaps():
    prompt = build_contextual_prompt(
        BASE,
        mode="surroundings",
        context={"prev_scene": "hallway", "elapsed_ms": 120_000},
    )
    assert "分钟" in prompt


def test_non_dict_context_is_ignored():
    assert build_contextual_prompt(BASE, mode="surroundings", context="oops") == BASE


def test_local_vision_context_is_included_without_prior_scene():
    prompt = build_contextual_prompt(
        BASE,
        mode="walking",
        context={"local_vision": "疑似有人在正前方"},
    )

    assert prompt.startswith(BASE)
    assert "iPhone 本地快速感知" in prompt
    assert "疑似有人在正前方" in prompt
    assert "不是最终判断" in prompt
    assert "当前图像为准" in prompt
