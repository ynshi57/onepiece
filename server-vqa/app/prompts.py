"""Prompt templates for visual-assistance modes.

Templates live on the backend (not hardcoded in the iOS app) so prompts can be
tuned by editing this file, or an external JSON file pointed to by the
``VQA_PROMPTS_FILE`` env var, and restarting the service -- no iOS rebuild.
"""

import json
import logging
import os


logger = logging.getLogger(__name__)

DEFAULT_MODE = "surroundings"

# Keep these in sync with the mode raw values sent by the iOS app
# (AssistanceMode.rawValue).
_BUILTIN_TEMPLATES = {
    "surroundings": (
        "模式=周围。用中文描述整体场景和空间布局。请明确左侧、正前方、右侧分别有什么；"
        "先说场景类型，再说最重要的物体和位置。不要只列物体。"
    ),
    "walking": (
        "模式=行走。只关注安全通行。请判断左前方、正前方、右前方是否有人、车辆、台阶、门、"
        "障碍物、边缘或可通行空间。输出简短风险等级、方向感和下一步行动建议。不要描述无关细节。"
    ),
    "readText": (
        "模式=读文字。请优先读取画面文字，保持原文顺序。若文字不清楚，请说明应该更靠近、"
        "对准或增加光线。"
    ),
    "detail": (
        "模式=详细。请用中文较详细描述画面：场景、左中右空间关系、近处和远处物体、文字、"
        "可能风险、以及建议行动。"
    ),
}


def _load_overrides() -> dict:
    """Load prompt template overrides from the JSON file, if configured.

    Failures are logged and ignored so a bad override file cannot take the
    service down; the built-in templates remain in effect.
    """
    path = os.getenv("VQA_PROMPTS_FILE", "").strip()
    if not path:
        return {}

    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("Failed to load VQA_PROMPTS_FILE=%s: %s", path, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning("VQA_PROMPTS_FILE=%s did not contain a JSON object; ignoring.", path)
        return {}

    overrides = {
        str(key): value.strip()
        for key, value in data.items()
        if isinstance(value, str) and value.strip()
    }
    return overrides


def get_templates() -> dict:
    """Return built-in templates merged with any configured overrides."""
    templates = dict(_BUILTIN_TEMPLATES)
    templates.update(_load_overrides())
    return templates


def resolve_prompt(mode: str = "", question: str = "", legacy_prompt: str = "") -> str:
    """Build the final prompt sent to the model.

    Precedence for the base instruction:
      1. A known ``mode`` -> its template (backend-configurable path).
      2. A non-empty ``legacy_prompt`` -> used as-is (older iOS clients that
         still send the full prompt text).
      3. The default mode template.

    A non-empty ``question`` is always appended so free-form user questions
    take priority in the answer.
    """
    templates = get_templates()
    mode = (mode or "").strip()
    question = (question or "").strip()
    legacy_prompt = (legacy_prompt or "").strip()

    if mode and mode in templates:
        base = templates[mode]
    elif legacy_prompt:
        base = legacy_prompt
    else:
        base = templates[DEFAULT_MODE]

    if question:
        base = (
            f"{base}\n\n用户的具体问题：{question}\n"
            "请优先、直接回答这个问题，再补充必要的安全提示。"
        )

    return base
