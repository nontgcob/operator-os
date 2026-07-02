from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel, Field
from redis import Redis

app = FastAPI(title="OperatorOS SAM3 Service", version="0.1.0")

redis_client = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


class TrackingStartRequest(BaseModel):
    tracking_job_id: str
    session_id: str
    video_id: str
    timestamp: float
    frame_data_url: str
    question: str
    segmentation_prompt: str = ""
    annotations: list[dict[str, Any]] = Field(default_factory=list)


def _overlay_at(t: float, offset: float) -> list[dict[str, Any]]:
    x = 20 + (offset * 1.7)
    return [
        {
            "track_id": "lever-1",
            "label": "Tracked Lever",
            "color": "#67A552",
            "timestamp": t,
            "points": [
                {"x": x, "y": 35},
                {"x": x + 18, "y": 35},
                {"x": x + 18, "y": 62},
                {"x": x, "y": 62},
            ],
        }
    ]


async def _run_tracking(payload: TrackingStartRequest) -> None:
    overlays: list[dict[str, Any]] = []
    for step in range(1, 11):
        overlays.extend(_overlay_at(payload.timestamp + (step * 0.5), float(step)))
        redis_client.setex(
            f"tracking:{payload.tracking_job_id}",
            3600,
            json.dumps({"done": False, "progress": step * 10, "overlays": overlays}),
        )
        await asyncio.sleep(0.35)
    redis_client.setex(
        f"tracking:{payload.tracking_job_id}",
        3600,
        json.dumps({"done": True, "progress": 100, "overlays": overlays}),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tracking/start")
async def tracking_start(payload: TrackingStartRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    background_tasks.add_task(_run_tracking, payload)
    return {"status": "started", "tracking_job_id": payload.tracking_job_id}
