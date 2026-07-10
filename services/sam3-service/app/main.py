from __future__ import annotations

import json
import os
from typing import Any

from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel, Field
from redis import Redis

from app.tracking_backend import TrackingBackend, TrackingJob, build_tracking_backend, tracking_error_payload

app = FastAPI(title="OperatorOS SAM3 Service", version="0.1.0")

redis_client = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
TRACKING_TTL_SECONDS = 3600
_tracking_backend: TrackingBackend | None = None


class TrackingStartRequest(BaseModel):
    tracking_job_id: str
    session_id: str
    video_id: str
    timestamp: float
    frame_data_url: str
    question: str
    segmentation_prompt: str = ""
    annotations: list[dict[str, Any]] = Field(default_factory=list)


def get_tracking_backend() -> TrackingBackend:
    global _tracking_backend
    if _tracking_backend is None:
        _tracking_backend = build_tracking_backend()
    return _tracking_backend


def _tracking_job(payload: TrackingStartRequest) -> TrackingJob:
    return TrackingJob(
        tracking_job_id=payload.tracking_job_id,
        session_id=payload.session_id,
        video_id=payload.video_id,
        timestamp=payload.timestamp,
        frame_data_url=payload.frame_data_url,
        question=payload.question,
        segmentation_prompt=payload.segmentation_prompt,
        annotations=payload.annotations,
    )


def _store_tracking_update(tracking_job_id: str, payload: dict[str, Any]) -> None:
    redis_client.setex(
        f"tracking:{tracking_job_id}",
        TRACKING_TTL_SECONDS,
        json.dumps(payload),
    )


async def _run_tracking(payload: TrackingStartRequest) -> None:
    try:
        backend = get_tracking_backend()
        async for update in backend.track(_tracking_job(payload)):
            _store_tracking_update(payload.tracking_job_id, update)
    except Exception as exc:
        _store_tracking_update(
            payload.tracking_job_id,
            tracking_error_payload(
                code="tracking_backend_failed",
                message=str(exc),
                backend="unknown",
            ),
        )


@app.get("/health")
async def health() -> dict[str, Any]:
    backend_status = get_tracking_backend().status()
    return {
        "status": "ok" if backend_status.ready else "degraded",
        "backend": backend_status.backend,
        "backend_ready": backend_status.ready,
        "backend_error": backend_status.code,
        "backend_message": backend_status.message,
    }


@app.post("/tracking/start")
async def tracking_start(payload: TrackingStartRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    background_tasks.add_task(_run_tracking, payload)
    return {"status": "started", "tracking_job_id": payload.tracking_job_id}
