from __future__ import annotations

import json
import os
import re
import asyncio
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.tracking_backend import (
    TrackingBackend,
    TrackingJob,
    build_tracking_backend,
    render_tracking_export,
    tracking_cancelled_payload,
    tracking_error_payload,
)

try:
    from services.common.env import load_env_file
except ImportError:
    load_env_file = None

if load_env_file:
    load_env_file()

TRACKING_TTL_SECONDS = 3600
USE_REDIS_STATE = os.getenv("USE_REDIS_STATE", "false").lower() == "true"
_tracking_backend: TrackingBackend | None = None
_tracking_cancel_events: dict[str, threading.Event] = {}
_warmup_status: dict[str, str | None] = {"state": "pending", "error": None}


@asynccontextmanager
async def lifespan(_: FastAPI):
    backend = get_tracking_backend()
    warmup = getattr(backend, "warmup", None)
    if callable(warmup):
        _warmup_status.update(state="warming", error=None)
        try:
            await warmup()
            _warmup_status.update(state="ready", error=None)
        except Exception as exc:
            _warmup_status.update(state="error", error=str(exc))
    else:
        _warmup_status.update(state="not_applicable", error=None)
    yield


app = FastAPI(title="OperatorOS SAM3 Service", version="0.1.0", lifespan=lifespan)


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
    targets: list[dict[str, Any]] = Field(default_factory=list)


class TrackingExportLayer(BaseModel):
    job_id: str = Field(pattern=r"^[A-Za-z0-9-]+$")
    track_ids: list[str] = Field(min_length=1)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    label: str = "Tracked object"


class TrackingExportRequest(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    layers: list[TrackingExportLayer] = Field(min_length=1)


def get_tracking_backend() -> TrackingBackend:
    global _tracking_backend
    if _tracking_backend is None:
        _tracking_backend = build_tracking_backend()
    return _tracking_backend


def _tracking_job(payload: TrackingStartRequest, cancel_event: threading.Event) -> TrackingJob:
    return TrackingJob(
        tracking_job_id=payload.tracking_job_id,
        session_id=payload.session_id,
        video_id=payload.video_id,
        timestamp=payload.timestamp,
        frame_data_url=payload.frame_data_url,
        question=payload.question,
        segmentation_prompt=payload.segmentation_prompt,
        annotations=payload.annotations,
        targets=payload.targets,
        cancel_event=cancel_event,
    )


def _store_tracking_update(tracking_job_id: str, payload: dict[str, Any]) -> None:
    state_client.setex(
        f"tracking:{tracking_job_id}",
        TRACKING_TTL_SECONDS,
        json.dumps({"tracking_job_id": tracking_job_id, **payload}),
    )


def _tracking_overlay_path(tracking_job_id: str) -> Path:
    output_root = Path(os.getenv("SAM3_RENDERED_VIDEO_ROOT", "./data/tracking")).expanduser()
    return output_root / f"{tracking_job_id}.overlays.json"


def _store_tracking_overlays(tracking_job_id: str, overlays: list[dict[str, Any]]) -> Path:
    output_path = _tracking_overlay_path(tracking_job_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    working_path = output_path.with_suffix(".working.json")
    working_path.write_text(
        json.dumps(
            {
                "tracking_job_id": tracking_job_id,
                "overlay_count": len(overlays),
                "overlays": overlays,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    working_path.replace(output_path)
    return output_path


async def _run_tracking(payload: TrackingStartRequest, cancel_event: threading.Event) -> None:
    collected_overlays: list[dict[str, Any]] = []
    overlay_keys: set[str] = set()
    try:
        backend = get_tracking_backend()
        async for update in backend.track(_tracking_job(payload, cancel_event)):
            if cancel_event.is_set() and not update.get("cancelled"):
                update = tracking_cancelled_payload(
                    getattr(backend, "name", "sam3"),
                    [
                        {
                            "id": target.get("id") or f"target-{index + 1}",
                            "label": target.get("label") or "Tracked object",
                            "color": target.get("color") or "#67A552",
                        }
                        for index, target in enumerate(payload.targets)
                    ],
                )
            for overlay in update.get("overlays", []):
                overlay_key = json.dumps(overlay, sort_keys=True, separators=(",", ":"))
                if overlay_key not in overlay_keys:
                    overlay_keys.add(overlay_key)
                    collected_overlays.append(overlay)
            if update.get("done") and not update.get("error") and not update.get("cancelled"):
                _store_tracking_overlays(payload.tracking_job_id, collected_overlays)
                update = {**update, "overlay_count": len(collected_overlays)}
            _store_tracking_update(payload.tracking_job_id, update)
            if update.get("cancelled"):
                break
    except Exception as exc:
        _store_tracking_update(
            payload.tracking_job_id,
            tracking_error_payload(
                code="tracking_backend_failed",
                message=str(exc),
                backend="unknown",
            ),
        )
    finally:
        _tracking_cancel_events.pop(payload.tracking_job_id, None)


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
        "status": "ok" if backend_status.ready and _warmup_status["state"] != "error" else "degraded",
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
        "warmup_state": _warmup_status["state"],
        "warmup_error": _warmup_status["error"],
        "simulation_enabled": os.getenv("SAM3_TRACKING_BACKEND", "sam3").strip().lower() == "simulation"
        and os.getenv("SAM3_ALLOW_SIMULATION_FALLBACK", "false").lower() == "true",
    }


@app.post("/tracking/start")
async def tracking_start(payload: TrackingStartRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    cancel_event = threading.Event()
    _tracking_cancel_events[payload.tracking_job_id] = cancel_event
    target_progress = [
        {
            "target_id": target.get("id") or f"target-{index + 1}",
            "label": target.get("label") or "Tracked object",
            "progress": 0,
            "stage": "queued",
            "color": target.get("color") or "#67A552",
        }
        for index, target in enumerate(payload.targets)
    ]
    _store_tracking_update(
        payload.tracking_job_id,
        {
            "done": False,
            "progress": 0,
            "stage": "queued",
            "target_progress": target_progress,
            "overlays": [],
            "backend": "pending",
        },
    )
    background_tasks.add_task(_run_tracking, payload, cancel_event)
    return {"status": "started", "tracking_job_id": payload.tracking_job_id}


@app.post("/tracking/cancel/{tracking_job_id}")
async def cancel_tracking(tracking_job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9-]+", tracking_job_id):
        raise HTTPException(status_code=400, detail="Invalid tracking job ID")

    current_raw = state_client.get(f"tracking:{tracking_job_id}")
    if not current_raw:
        raise HTTPException(status_code=404, detail="Tracking job was not found or expired")
    current = json.loads(current_raw)
    if current.get("done"):
        return current

    cancel_event = _tracking_cancel_events.get(tracking_job_id)
    if cancel_event:
        cancel_event.set()
    cancelled = tracking_cancelled_payload(
        current.get("backend") or "sam3",
        [
            {
                "id": target.get("target_id"),
                "label": target.get("label"),
                "color": target.get("color"),
            }
            for target in current.get("target_progress", [])
        ],
    )
    _store_tracking_update(tracking_job_id, cancelled)
    return {"tracking_job_id": tracking_job_id, **cancelled}


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


@app.get("/tracking/clean-video/{tracking_job_id}")
async def clean_tracking_video(tracking_job_id: str) -> FileResponse:
    if not re.fullmatch(r"[A-Za-z0-9-]+", tracking_job_id):
        raise HTTPException(status_code=400, detail="Invalid tracking job ID")
    output_root = Path(os.getenv("SAM3_RENDERED_VIDEO_ROOT", "./data/tracking")).expanduser()
    video_path = output_root / f"{tracking_job_id}.clean.mp4"
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Clean tracking clip was not found")
    return FileResponse(video_path, media_type="video/mp4", filename=f"{tracking_job_id}.clean.mp4")


@app.get("/tracking/overlays/{tracking_job_id}")
async def tracking_overlays(tracking_job_id: str) -> FileResponse:
    if not re.fullmatch(r"[A-Za-z0-9-]+", tracking_job_id):
        raise HTTPException(status_code=400, detail="Invalid tracking job ID")
    overlay_path = _tracking_overlay_path(tracking_job_id)
    if not overlay_path.is_file():
        raise HTTPException(status_code=404, detail="Tracking overlays were not found")
    return FileResponse(overlay_path, media_type="application/json", filename=f"{tracking_job_id}.overlays.json")


@app.post("/tracking/export")
async def export_tracking_video(payload: TrackingExportRequest) -> FileResponse:
    selected_overlays: list[dict[str, Any]] = []
    for layer in payload.layers:
        overlay_path = _tracking_overlay_path(layer.job_id)
        if not overlay_path.is_file():
            raise HTTPException(status_code=404, detail=f"Tracking overlays were not found for {layer.job_id}")
        manifest = json.loads(overlay_path.read_text(encoding="utf-8"))
        selected_track_ids = set(layer.track_ids)
        selected_overlays.extend(
            {**overlay, "color": layer.color, "target_color": layer.color}
            for overlay in manifest.get("overlays", [])
            if overlay.get("track_id") in selected_track_ids
        )

    if not selected_overlays:
        raise HTTPException(status_code=400, detail="The selected tracking items do not contain any masks")

    video_root = Path(os.getenv("SAM3_VIDEO_ROOT", "data/video")).expanduser().resolve()
    video_path = (video_root / payload.video_id / "source.mp4").resolve()
    if video_root not in video_path.parents or not video_path.is_file():
        raise HTTPException(status_code=404, detail="Source video was not found")

    output_root = Path(os.getenv("SAM3_RENDERED_VIDEO_ROOT", "./data/tracking")).expanduser()
    export_id = str(uuid4())
    output_path = output_root / f"export-{export_id}.mp4"
    try:
        await asyncio.to_thread(render_tracking_export, video_path, output_path, selected_overlays)
    except (OSError, RuntimeError, ValueError) as exc:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Unable to export tracking video: {exc}") from exc

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"operatoros-tracking-{export_id[:8]}.mp4",
        background=BackgroundTask(output_path.unlink, missing_ok=True),
    )


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
