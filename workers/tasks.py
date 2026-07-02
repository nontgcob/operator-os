from __future__ import annotations

import json
import os
import time
from typing import Any

from redis import Redis

redis_client = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


def run_tracking_job(job_payload: dict[str, Any]) -> dict[str, Any]:
    tracking_job_id = job_payload["tracking_job_id"]
    overlays: list[dict[str, Any]] = []
    base_timestamp = float(job_payload.get("timestamp", 0))
    for step in range(1, 11):
        overlays.append(
            {
                "track_id": "worker-track-1",
                "label": "Worker Overlay",
                "color": "#5177AA",
                "timestamp": base_timestamp + (step * 0.5),
                "points": [
                    {"x": 30 + step, "y": 20},
                    {"x": 45 + step, "y": 20},
                    {"x": 45 + step, "y": 45},
                    {"x": 30 + step, "y": 45},
                ],
            }
        )
        redis_client.setex(
            f"tracking:{tracking_job_id}",
            3600,
            json.dumps({"done": False, "progress": step * 10, "overlays": overlays}),
        )
        time.sleep(0.3)

    payload = {"done": True, "progress": 100, "overlays": overlays}
    redis_client.setex(f"tracking:{tracking_job_id}", 3600, json.dumps(payload))
    return payload
