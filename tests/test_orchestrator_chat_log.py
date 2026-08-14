from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import sys

ORCH_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "orchestrator" / "app"
sys.path.append(str(ORCH_APP_PATH))
from chat_log import ChatLog  # type: ignore  # noqa: E402


def test_chat_log_appends_complete_unicode_sql_records(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "chat.sqlite3"
    chat_log = ChatLog(log_path)

    chat_log.append_message(
        session_id="session-1",
        exchange_id="exchange-1",
        role="user",
        content="What is shown? hello",
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

    connection = sqlite3.connect(log_path)
    connection.row_factory = sqlite3.Row
    records = connection.execute("SELECT * FROM chat_messages ORDER BY id").fetchall()
    assert len(records) == 2
    assert records[0]["content"] == "What is shown? hello"
    assert json.loads(records[0]["context_json"]) == {"video_id": "video-1"}
    assert records[1]["role"] == "assistant"
    assert records[1]["status"] == "completed"
    assert records[0]["recorded_at"].endswith("+00:00")


def test_chat_log_appends_analytics_events(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "chat.sqlite3"
    chat_log = ChatLog(log_path)

    chat_log.append_event(
        event_type="tracking_started",
        session_id="session-1",
        video_id="video-1",
        tracking_job_id="track-1",
        payload={"timestamp": 12.5},
    )

    connection = sqlite3.connect(log_path)
    connection.row_factory = sqlite3.Row
    record = connection.execute("SELECT * FROM analytics_events").fetchone()
    assert record["event_type"] == "tracking_started"
    assert record["tracking_job_id"] == "track-1"
    assert json.loads(record["payload_json"]) == {"timestamp": 12.5}
