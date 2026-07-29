from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ComparisonStore:
    """Durable, server-side storage for blinded RAG comparisons."""

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
                CREATE TABLE IF NOT EXISTS comparisons (
                    comparison_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mapping_json TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    answers_json TEXT NOT NULL,
                    selected_label TEXT,
                    revealed_at TEXT,
                    retry_of TEXT
                )
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "comparison_id": row["comparison_id"],
            "session_id": row["session_id"],
            "question": row["question"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status": row["status"],
            "mapping": json.loads(row["mapping_json"]),
            "request": json.loads(row["request_json"]),
            "answers": json.loads(row["answers_json"]),
            "selected_label": row["selected_label"],
            "revealed_at": row["revealed_at"],
            "retry_of": row["retry_of"],
        }

    def create(
        self,
        *,
        comparison_id: str,
        session_id: str,
        question: str,
        mapping: dict[str, str],
        request: dict[str, Any],
        retry_of: str | None = None,
    ) -> dict[str, Any]:
        if set(mapping) != {"A", "B"} or set(mapping.values()) != {"text_rag", "multimodal_rag"}:
            raise ValueError("mapping must assign text_rag and multimodal_rag to A and B")
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO comparisons (
                    comparison_id, session_id, question, created_at, updated_at,
                    status, mapping_json, request_json, answers_json, retry_of
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, '{}', ?)
                """,
                (
                    comparison_id,
                    session_id,
                    question,
                    now,
                    now,
                    json.dumps(mapping, separators=(",", ":")),
                    json.dumps(request, ensure_ascii=False, separators=(",", ":")),
                    retry_of,
                ),
            )
        return self.get(comparison_id)

    def get(self, comparison_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM comparisons WHERE comparison_id = ?",
                (comparison_id,),
            ).fetchone()
        if row is None:
            raise KeyError(comparison_id)
        return self._decode(row)

    def mapping_counts(self, session_id: str) -> dict[str, int]:
        counts = {"text_left": 0, "multimodal_left": 0}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT mapping_json FROM comparisons WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        for row in rows:
            try:
                mapping = json.loads(row["mapping_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if mapping.get("A") == "text_rag":
                counts["text_left"] += 1
            elif mapping.get("A") == "multimodal_rag":
                counts["multimodal_left"] += 1
        return counts

    def record_answer(
        self,
        comparison_id: str,
        *,
        label: str,
        answer: dict[str, Any],
    ) -> dict[str, Any]:
        if label not in {"A", "B"}:
            raise ValueError("label must be A or B")
        with self._lock:
            record = self.get(comparison_id)
            if record["selected_label"]:
                raise ValueError("revealed comparisons cannot be modified")
            answers = record["answers"]
            answers[label] = answer
            statuses = {value.get("status") for value in answers.values() if isinstance(value, dict)}
            if len(answers) == 2:
                status = "completed" if statuses == {"completed"} else "partial"
            else:
                status = "running"
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE comparisons
                    SET answers_json = ?, status = ?, updated_at = ?
                    WHERE comparison_id = ?
                    """,
                    (
                        json.dumps(answers, ensure_ascii=False, separators=(",", ":")),
                        status,
                        _utc_now(),
                        comparison_id,
                    ),
                )
        return self.get(comparison_id)

    def reveal(self, comparison_id: str, selected_label: str) -> dict[str, Any]:
        if selected_label not in {"A", "B"}:
            raise ValueError("selected_label must be A or B")
        with self._lock:
            record = self.get(comparison_id)
            if record["status"] != "completed":
                raise ValueError("only completed comparisons can be revealed")
            previous = record["selected_label"]
            if previous and previous != selected_label:
                raise ValueError("the recorded comparison choice cannot be changed")
            if not previous:
                now = _utc_now()
                with self._connect() as connection:
                    connection.execute(
                        """
                        UPDATE comparisons
                        SET selected_label = ?, revealed_at = ?, updated_at = ?
                        WHERE comparison_id = ?
                        """,
                        (selected_label, now, now, comparison_id),
                    )
        return self.get(comparison_id)
