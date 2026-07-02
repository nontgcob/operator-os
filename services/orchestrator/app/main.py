from __future__ import annotations

import json
import os
import time
import asyncio
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from redis import Redis
from rq import Queue
from memory import append_rolling_conversation

app = FastAPI(title="OperatorOS Orchestrator", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RAGVLM_SERVICE_URL = os.getenv("RAGVLM_SERVICE_URL", "http://localhost:8001")
VIDEO_SERVICE_URL = os.getenv("VIDEO_SERVICE_URL", "http://localhost:8002")
SAM3_SERVICE_URL = os.getenv("SAM3_SERVICE_URL", "http://localhost:8003")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = Redis.from_url(REDIS_URL, decode_responses=True)
tracking_queue = Queue("tracking", connection=Redis.from_url(REDIS_URL))


def _log_event(event_type: str, **fields: Any) -> None:
    print(json.dumps({"event": event_type, "ts": time.time(), **fields}))


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class TranscriptWindow(BaseModel):
    timestamp: float
    start: float
    end: float
    segments: list[TranscriptSegment]


class ChatStreamRequest(BaseModel):
    session_id: str
    video_id: str
    timestamp: float
    frame_data_url: str
    question: str
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    transcript_window: TranscriptWindow
    document_ids: list[str] = Field(default_factory=list)


class TrackingStartRequest(BaseModel):
    session_id: str
    video_id: str
    timestamp: float
    frame_data_url: str
    question: str
    annotations: list[dict[str, Any]] = Field(default_factory=list)


def _build_segmentation_prompt(question: str, annotations: list[dict[str, Any]]) -> str:
    if annotations:
        return f"Track the operator-referenced object related to: {question}"
    return f"Track the primary object relevant to question: {question}"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/media/ingest")
async def media_ingest(file: UploadFile = File(default=None), youtube_url: str | None = None) -> Any:
    if not file and not youtube_url:
        raise HTTPException(status_code=400, detail="Provide file or youtube_url")

    async with httpx.AsyncClient(timeout=180) as client:
        if file:
            payload = {"file": (file.filename, await file.read(), file.content_type)}
            response = await client.post(f"{VIDEO_SERVICE_URL}/media/ingest", files=payload)
        else:
            response = await client.post(
                f"{VIDEO_SERVICE_URL}/media/ingest",
                json={"youtube_url": youtube_url},
            )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    _log_event("media_ingested", source="file" if file else "youtube")
    return response.json()


@app.get("/transcript/window")
async def transcript_window(video_id: str, timestamp: float, before: float = 30, after: float = 15) -> Any:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            f"{VIDEO_SERVICE_URL}/transcript/window",
            params={"video_id": video_id, "timestamp": timestamp, "before": before, "after": after},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    _log_event("transcript_window_fetched", video_id=video_id, timestamp=timestamp)
    return response.json()


@app.post("/chat/stream")
async def chat_stream(payload: ChatStreamRequest) -> StreamingResponse:
    _log_event("inference_started", session_id=payload.session_id, video_id=payload.video_id, timestamp=payload.timestamp)
    request_body = {
        "question": payload.question,
        "frame_data_url": payload.frame_data_url,
        "annotations": payload.annotations,
        "transcript_segments": [segment.model_dump() for segment in payload.transcript_window.segments],
        "retrieved_chunks": (
            [f"Attached document context placeholder for {doc_id}" for doc_id in payload.document_ids]
            if payload.document_ids
            else []
        ),
        "conversation": _load_conversation(payload.session_id),
    }

    async def stream() -> Any:
        full_text = ""
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{RAGVLM_SERVICE_URL}/ragvlm/infer",
                json=request_body,
            ) as response:
                if response.status_code >= 400:
                    text = await response.aread()
                    raise HTTPException(status_code=response.status_code, detail=text.decode())
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:]
                    if chunk == "[DONE]":
                        break
                    full_text += chunk
                    yield f"data: {chunk}\n\n"
                _append_conversation(payload.session_id, payload.question, full_text)
                _log_event("inference_completed", session_id=payload.session_id, answer_len=len(full_text))
                yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/tracking/start")
async def tracking_start(payload: TrackingStartRequest) -> dict[str, str]:
    tracking_job_id = str(uuid4())
    redis_client.setex(
        f"tracking:{tracking_job_id}",
        3600,
        json.dumps({"done": False, "overlays": []}),
    )
    use_worker = os.getenv("USE_WORKER_QUEUE", "false").lower() == "true"
    segmentation_prompt = _build_segmentation_prompt(payload.question, payload.annotations)
    if use_worker:
        tracking_queue.enqueue(
            "workers.tasks.run_tracking_job",
            {**payload.model_dump(), "tracking_job_id": tracking_job_id, "segmentation_prompt": segmentation_prompt},
        )
    else:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{SAM3_SERVICE_URL}/tracking/start",
                json={**payload.model_dump(), "tracking_job_id": tracking_job_id, "segmentation_prompt": segmentation_prompt},
            )
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
    _log_event("tracking_started", tracking_job_id=tracking_job_id, session_id=payload.session_id)
    return {"tracking_job_id": tracking_job_id}


@app.get("/tracking/events/{tracking_job_id}")
async def tracking_events(tracking_job_id: str) -> StreamingResponse:
    async def stream() -> Any:
        last_payload = ""
        for _ in range(120):
            payload = redis_client.get(f"tracking:{tracking_job_id}")
            if payload and payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
                if json.loads(payload).get("done"):
                    break
            yield ": keepalive\n\n"
            await asyncio.sleep(0.5)
        yield "data: {\"done\": true, \"overlays\": []}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


def _load_conversation(session_id: str) -> list[dict[str, str]]:
    raw = redis_client.get(f"conversation:{session_id}")
    if not raw:
        return []
    return json.loads(raw)


def _append_conversation(session_id: str, question: str, answer: str) -> None:
    current = _load_conversation(session_id)
    updated = append_rolling_conversation(current, question, answer, max_messages=12)
    redis_client.setex(f"conversation:{session_id}", 3600, json.dumps(updated))
