from __future__ import annotations

import json
from typing import Any

DONE_SENTINEL = "[DONE]"


def parse_openrouter_sse_line(line: str) -> str | None:
    if not line.startswith("data: "):
        return None
    payload = line[6:].strip()
    if payload == DONE_SENTINEL:
        return DONE_SENTINEL
    try:
        parsed: dict[str, Any] = json.loads(payload)
    except json.JSONDecodeError:
        return None
    choices = parsed.get("choices")
    if not choices:
        return None
    delta = choices[0].get("delta", {})
    content = delta.get("content")
    return content if isinstance(content, str) else None
