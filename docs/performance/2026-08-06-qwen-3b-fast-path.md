# Qwen 3B fast path for walking / surroundings

Date: 2026-08-06

## Decision

For `walking` and `surroundings` frames without an explicit user question, the backend uses a compact safety JSON schema and a smaller decode ceiling. Incremental frames default to sending only the current image plus text scene context; the previous image is no longer sent to Qwen unless `QWEN_SEND_PREVIOUS_IMAGE_IN_INCREMENTAL=1` is explicitly set.

## Why

Qwen 3B latency is dominated by vision prefill and then JSON decode. Sending `previous_image_base64` makes the runtime process two images for continuity frames, which is too expensive for walking assistance. Users need a fast safety answer first, not a verbose diagnostic object.

## Safety boundary

The fast schema keeps the user-critical fields:

- objects
- scene
- summary
- spatial_description
- risk_level
- risk_message
- suggested_action
- spoken_text
- change_significance
- changes

It drops verbose fields that fusion can safely fill (`description`, `ocr_text`) only for fast safety modes. `readText`, `detail`, and explicit user questions keep the full schema.

## Runtime knobs

- `QWEN_MAX_TOKENS_FAST` default: `260`
- `QWEN_MAX_TOKENS_FULL` default: `520`
- `QWEN_SEND_PREVIOUS_IMAGE_IN_INCREMENTAL` default: `0`

Opt into previous-image comparison only for lab evaluation, not the default walking experience.
