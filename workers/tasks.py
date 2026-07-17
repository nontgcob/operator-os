from __future__ import annotations

import json
import os
from typing import Any

import httpx

try:
    from redis import Redis
except ImportError:
    Redis = None  # type: ignore[assignment]


def _build_redis_client() -> Any:
    if Redis is None:
        return None
    return Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


redis_client = _build_redis_client()
SAM3_SERVICE_URL = os.getenv("SAM3_SERVICE_URL", "http://localhost:8003")
TRACKING_TTL_SECONDS = 3600


def _store_tracking_update(tracking_job_id: str, payload: dict[str, Any]) -> None:
    if redis_client is None:
        return
    redis_client.setex(
        f"tracking:{tracking_job_id}",
        TRACKING_TTL_SECONDS,
        json.dumps(payload),
    )


def _tracking_error_payload(code: str, message: str) -> dict[str, Any]:
    return {
        "done": True,
        "progress": 0,
        "overlays": [],
        "backend": "worker",
        "error": {
            "code": code,
            "message": message,
        },
    }


def run_tracking_job(job_payload: dict[str, Any]) -> dict[str, Any]:
    tracking_job_id = job_payload["tracking_job_id"]
    try:
        with httpx.Client(timeout=120) as client:
            response = client.post(f"{SAM3_SERVICE_URL}/tracking/start", json=job_payload)
    except httpx.HTTPError as exc:
        payload = _tracking_error_payload("sam3_worker_proxy_failed", str(exc))
        _store_tracking_update(tracking_job_id, payload)
        return payload

    if response.status_code >= 400:
        payload = _tracking_error_payload(
            "sam3_worker_proxy_rejected",
            response.text or f"SAM3 service returned HTTP {response.status_code}",
        )
        _store_tracking_update(tracking_job_id, payload)
        return payload

    return {
        "status": "delegated",
        "tracking_job_id": tracking_job_id,
    }
