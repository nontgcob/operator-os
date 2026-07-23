from __future__ import annotations

import json
import os
import re
import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.tracking_backend import TrackingBackend, TrackingJob, build_tracking_backend, tracking_error_payload

try:
    from services.common.env import load_env_file
except ImportError:
    load_env_file = None

if load_env_file:
    load_env_file()

app = FastAPI(title="OperatorOS SAM3 Service", version="0.1.0")

TRACKING_TTL_SECONDS = 3600
USE_REDIS_STATE = os.getenv("USE_REDIS_STATE", "false").lower() == "true"
_tracking_backend: TrackingBackend | None = None


class _MemoryState:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        item = self._values.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at <= time.time():
            self._values.pop(key, None)
            return None
        return value

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._values[key] = (time.time() + ttl, value)


def _build_state_client() -> Any:
    if not USE_REDIS_STATE:
        return _MemoryState()
    try:
        from redis import Redis
    except ImportError as exc:
        raise RuntimeError("USE_REDIS_STATE=true requires the redis Python package") from exc
    return Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


state_client = _build_state_client()


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
    state_client.setex(
        f"tracking:{tracking_job_id}",
        TRACKING_TTL_SECONDS,
        json.dumps({"tracking_job_id": tracking_job_id, **payload}),
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
    backend = get_tracking_backend()
    backend_status = backend.status()
    backend_config = getattr(backend, "config", None)
    checkpoint_path = Path(os.getenv("SAM3_CHECKPOINT_PATH", "./models/sam3.pt")).expanduser()
    cuda_available: bool | None = None
    cuda_version: str | None = None
    gpu_name: str | None = None
    torch_version: str | None = None
    try:
        import torch

        torch_version = torch.__version__
        cuda_version = torch.version.cuda
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            gpu_name = torch.cuda.get_device_name(0)
    except (ImportError, RuntimeError):
        cuda_available = None
    return {
        "status": "ok" if backend_status.ready else "degraded",
        "backend": backend_status.backend,
        "backend_ready": backend_status.ready,
        "backend_error": backend_status.code,
        "backend_message": backend_status.message,
        "checkpoint_path": str(checkpoint_path.resolve()),
        "checkpoint_exists": checkpoint_path.exists(),
        "device": os.getenv("SAM3_DEVICE") or None,
        "torch_version": torch_version,
        "cuda_version": cuda_version,
        "cuda_available": cuda_available,
        "gpu_name": gpu_name,
        "max_propagation_frames": getattr(backend_config, "max_frames", None),
        "image_size": getattr(backend_config, "image_size", None),
        "simulation_enabled": os.getenv("SAM3_TRACKING_BACKEND", "sam3").strip().lower() == "simulation"
        and os.getenv("SAM3_ALLOW_SIMULATION_FALLBACK", "false").lower() == "true",
    }


@app.post("/tracking/start")
async def tracking_start(payload: TrackingStartRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    _store_tracking_update(
        payload.tracking_job_id,
        {"done": False, "progress": 0, "overlays": [], "backend": "pending"},
    )
    background_tasks.add_task(_run_tracking, payload)
    return {"status": "started", "tracking_job_id": payload.tracking_job_id}


@app.get("/tracking/status/{tracking_job_id}")
async def tracking_status(tracking_job_id: str) -> dict[str, Any]:
    payload = state_client.get(f"tracking:{tracking_job_id}")
    if payload:
        return json.loads(payload)
    return {
        "tracking_job_id": tracking_job_id,
        "done": True,
        "progress": 0,
        "overlays": [],
        "backend": "unknown",
        "error": {
            "code": "tracking_job_not_found",
            "message": "Tracking job was not found or expired.",
        },
    }


@app.get("/tracking/video/{tracking_job_id}")
async def tracking_video(tracking_job_id: str) -> FileResponse:
    if not re.fullmatch(r"[A-Za-z0-9-]+", tracking_job_id):
        raise HTTPException(status_code=400, detail="Invalid tracking job ID")
    output_root = Path(os.getenv("SAM3_RENDERED_VIDEO_ROOT", "./data/tracking")).expanduser()
    video_path = output_root / f"{tracking_job_id}.mp4"
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Rendered tracking video was not found")
    return FileResponse(video_path, media_type="video/mp4", filename=f"{tracking_job_id}.mp4")


@app.get("/tracking/events/{tracking_job_id}")
async def tracking_events(tracking_job_id: str) -> StreamingResponse:
    async def stream() -> Any:
        last_payload = ""
        for _ in range(3600):
            payload = state_client.get(f"tracking:{tracking_job_id}")
            if payload and payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
                if json.loads(payload).get("done"):
                    break
            yield ": keepalive\n\n"
            await asyncio.sleep(0.5)
        if not last_payload:
            yield (
                "data: "
                + json.dumps(
                    {
                        "tracking_job_id": tracking_job_id,
                        "done": True,
                        "progress": 0,
                        "overlays": [],
                        "backend": "unknown",
                        "error": {
                            "code": "tracking_job_not_found",
                            "message": "Tracking job was not found or expired.",
                        },
                    }
                )
                + "\n\n"
            )

    return StreamingResponse(stream(), media_type="text/event-stream")
