from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class ChatLog:
    """Append-only, durable JSONL storage for chat messages."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()

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
        record = {
            "schema_version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "exchange_id": exchange_id,
            "role": role,
            "content": content,
            "status": status,
            "context": context or {},
        }
        serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"))

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.write("\n")
