from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from workers import tasks


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, tuple[int, str]] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = (ttl, value)


def test_worker_tracking_delegates_to_sam3_service(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
            requests.append({"url": url, "json": json, "timeout": self.timeout})
            return httpx.Response(200, json={"status": "started", "tracking_job_id": "job-1"})

    monkeypatch.setattr(tasks.httpx, "Client", FakeClient)
    monkeypatch.setattr(tasks, "SAM3_SERVICE_URL", "http://sam3-service:8003")

    payload = {"tracking_job_id": "job-1", "video_id": "video-1"}
    result = tasks.run_tracking_job(payload)

    assert result == {"status": "delegated", "tracking_job_id": "job-1"}
    assert requests == [
        {
            "url": "http://sam3-service:8003/tracking/start",
            "json": payload,
            "timeout": 120,
        }
    ]


def test_worker_tracking_records_proxy_error(monkeypatch) -> None:
    fake_redis = FakeRedis()

    class FailingClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FailingClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
            raise httpx.ConnectError("SAM3 unavailable")

    monkeypatch.setattr(tasks.httpx, "Client", FailingClient)
    monkeypatch.setattr(tasks, "redis_client", fake_redis)

    result = tasks.run_tracking_job({"tracking_job_id": "job-2"})

    _, raw_payload = fake_redis.values["tracking:job-2"]
    stored_payload = json.loads(raw_payload)
    assert result == stored_payload
    assert stored_payload["done"] is True
    assert stored_payload["overlays"] == []
    assert stored_payload["error"]["code"] == "sam3_worker_proxy_failed"
