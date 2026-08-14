from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class ChatLog:
    """Append-only, durable SQLite storage for chat and analytics events."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    exchange_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    context_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_messages_session_recorded
                ON chat_messages (session_id, recorded_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    session_id TEXT,
                    event_type TEXT NOT NULL,
                    exchange_id TEXT,
                    video_id TEXT,
                    tracking_job_id TEXT,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_analytics_events_session_recorded
                ON analytics_events (session_id, recorded_at)
                """
            )

    def append_message(
        self,
        *,
        session_id: str,
        exchange_id: str,
        role: str,
        content: str,
        status: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        recorded_at = datetime.now(timezone.utc).isoformat()
        context_json = json.dumps(context or {}, ensure_ascii=False, separators=(",", ":"))

        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO chat_messages (
                        schema_version, recorded_at, session_id, exchange_id,
                        role, content, status, context_json
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recorded_at,
                        session_id,
                        exchange_id,
                        role,
                        content,
                        status,
                        context_json,
                    ),
                )

    def append_event(
        self,
        *,
        event_type: str,
        session_id: str | None = None,
        exchange_id: str | None = None,
        video_id: str | None = None,
        tracking_job_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        recorded_at = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))

        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO analytics_events (
                        schema_version, recorded_at, session_id, event_type,
                        exchange_id, video_id, tracking_job_id, payload_json
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recorded_at,
                        session_id,
                        event_type,
                        exchange_id,
                        video_id,
                        tracking_job_id,
                        payload_json,
                    ),
                )
