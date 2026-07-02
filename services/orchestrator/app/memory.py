from __future__ import annotations

from typing import Any


def append_rolling_conversation(
    current: list[dict[str, Any]],
    question: str,
    answer: str,
    max_messages: int = 12,
) -> list[dict[str, str]]:
    updated = list(current)
    updated.append({"role": "user", "content": question})
    updated.append({"role": "assistant", "content": answer})
    return updated[-max_messages:]
