"""Scene-continuity "skill" layer that sits between the mode prompt and Qwen.

The backend stays stateless: continuity comes from a ``context`` object the iOS
client echoes back on every frame (its own previous result plus a reverse-geocoded
place label). This module turns that context into an extra prompt block that tells
the model what the user was already told, so it can report **only important
changes** instead of repeating the same scene description every 2 seconds.

Pure functions only -- no I/O, no global state -- so both inference paths
(`app.signaling` direct, `app.worker_client` relay) can call it and it is trivial
to unit test.
"""

from typing import Optional


# How many previously-seen objects to echo back into the prompt. Keeps the added
# token count bounded so continuity does not defeat the latency work.
_MAX_PREV_OBJECTS = 8

# Cap the echoed previous summary so a runaway model response last frame cannot
# balloon this frame's prompt.
_MAX_PREV_SUMMARY_CHARS = 200


def _clean_str(value: object, max_chars: int = 0) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if max_chars and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "…"
    return cleaned


def _clean_objects(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    objects: list[str] = []
    for item in value:
        if isinstance(item, (str, int, float)):
            text = str(item).strip()
            if text:
                objects.append(text)
        if len(objects) >= _MAX_PREV_OBJECTS:
            break
    return objects


def _elapsed_phrase(context: dict) -> str:
    elapsed_ms = context.get("elapsed_ms")
    if not isinstance(elapsed_ms, (int, float)) or elapsed_ms <= 0:
        return ""
    seconds = elapsed_ms / 1000.0
    if seconds < 60:
        return f"距上次描述约 {seconds:.0f} 秒。"
    minutes = seconds / 60.0
    return f"距上次描述约 {minutes:.0f} 分钟。"


def build_contextual_prompt(
    base_prompt: str,
    mode: str = "",
    context: Optional[dict] = None,
) -> str:
    """Append a continuity block to ``base_prompt`` from the client-echoed context.

    Returns ``base_prompt`` unchanged when there is no usable context (first frame
    of a session, or an older client that does not send one) so behaviour is
    identical to the previous stateless path in that case.
    """
    base = (base_prompt or "").strip()
    if not isinstance(context, dict):
        return base

    prev_summary = _clean_str(context.get("prev_summary"), _MAX_PREV_SUMMARY_CHARS)
    prev_scene = _clean_str(context.get("prev_scene"))
    prev_objects = _clean_objects(context.get("prev_objects"))
    place_label = _clean_str(context.get("place_label"))

    # No meaningful prior state -> behave exactly like the stateless path.
    if not (prev_summary or prev_scene or prev_objects or place_label):
        return base

    lines: list[str] = ["", "【连续观察上下文】"]
    if place_label:
        lines.append(f"用户当前大致位置：{place_label}。")
    if prev_scene:
        lines.append(f"上次判断的场景：{prev_scene}。")
    if prev_objects:
        lines.append(f"上次已提到的物体：{'、'.join(prev_objects)}。")
    if prev_summary:
        lines.append(f"上次已告诉用户：{prev_summary}")

    elapsed = _elapsed_phrase(context)
    if elapsed:
        lines.append(elapsed)

    lines.append(
        "用户很可能还在同一地点，并且已经知道上面这些信息。"
        "请只描述与上次相比的重要变化（例如新出现或消失的人、车、障碍、门、台阶，"
        "或风险等级变化）。"
        "如果没有重要变化，把 change_significance 设为 none，changes 用一句话说明"
        "（例如“无明显变化”），不要重复完整场景描述。"
        "出现需要用户注意的新情况时，把 change_significance 设为 major。"
    )

    return base + "\n" + "\n".join(lines)
