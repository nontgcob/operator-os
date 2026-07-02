from __future__ import annotations

from pathlib import Path
import sys

ORCH_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "orchestrator" / "app"
sys.path.append(str(ORCH_APP_PATH))
from memory import append_rolling_conversation  # type: ignore  # noqa: E402


def test_conversation_rolling_window() -> None:
    conversation: list[dict[str, str]] = []
    for idx in range(8):
        conversation = append_rolling_conversation(conversation, f"q{idx}", f"a{idx}", max_messages=12)
    assert len(conversation) == 12
    assert conversation[0]["content"] == "q2"
    assert conversation[-1]["content"] == "a7"
