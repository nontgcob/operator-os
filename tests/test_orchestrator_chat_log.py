from __future__ import annotations

import json
from pathlib import Path
import sys

ORCH_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "orchestrator" / "app"
sys.path.append(str(ORCH_APP_PATH))
from chat_log import ChatLog  # type: ignore  # noqa: E402


def test_chat_log_appends_complete_unicode_jsonl_records(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "chat.jsonl"
    chat_log = ChatLog(log_path)

    chat_log.append_message(
        session_id="session-1",
        exchange_id="exchange-1",
        role="user",
        content="What is shown? 你好",
        status="received",
        context={"video_id": "video-1"},
    )
    chat_log.append_message(
        session_id="session-1",
        exchange_id="exchange-1",
        role="assistant",
        content="A control panel.",
        status="completed",
    )

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["content"] == "What is shown? 你好"
    assert records[0]["context"] == {"video_id": "video-1"}
    assert records[1]["role"] == "assistant"
    assert records[1]["status"] == "completed"
    assert records[0]["recorded_at"].endswith("+00:00")
