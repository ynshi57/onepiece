# VQASee positioning: general visual risk assistance

Date: 2026-08-06

## Decision

VQASee is no longer positioned only as a low-vision assistive app. It is a voice-first visual risk assistance product for:

- pedestrians;
- cyclists / e-bike riders;
- drivers who need non-driving-control risk reminders;
- low-vision users;
- anyone whose attention may drift while moving through real environments.

The product helps users notice risk, boundaries, road/crosswalk/lane cues, nearby people/vehicles/obstacles, signs and text. It must remain simple, fast and privacy-preserving.

## Safety boundary

VQASee is **not** an autonomous driving system, navigation authority, or replacement for user observation.

It may say:

- “右侧像是车道边界，请放慢。”
- “正前方可能有人，我正在确认。”
- “前方疑似人行横道，请自己确认交通情况。”

It must not promise:

- “可以走。”
- “可以开。”
- “前方安全。”
- “按这条轨迹驾驶。”

## Product implication

The app needs broader local perception than generic VQA:

- common obstacles: people, vehicles, bicycles, pets, boxes, signs;
- road / sidewalk / curb / crosswalk / lane marking cues;
- boundaries and uncertain passable regions;
- depth / approach risk when available.

Accessibility remains a first-class requirement. The product is broader than low vision, but low-vision usability is still the strictest UX test.

## Role implications

- Jobs: judge whether a feature helps real movement safety without adding cognitive load.
- Logan: prevent stale frames, queue buildup, unsafe latency and hidden failures.
- Siyu: make risk and uncertainty understandable at a glance and by voice.
- Quanmai: choose models/prompts/schemas that expose risk evidence and uncertainty, never fake certainty.
