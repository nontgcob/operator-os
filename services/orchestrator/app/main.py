from __future__ import annotations

import json
import os
import time
import asyncio
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from redis import Redis
from rq import Queue

try:
    from .memory import append_rolling_conversation
except ImportError:
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


def _env_positive_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number of seconds") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number of seconds")
    return value


MEDIA_INGEST_TIMEOUT_SECONDS = _env_positive_float("MEDIA_INGEST_TIMEOUT_SECONDS", 900.0)
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


class DocumentRetrieveRequest(BaseModel):
    question: str
    document_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=4, ge=1, le=12)


class TrackingStartRequest(BaseModel):
    session_id: str
    video_id: str
    timestamp: float
    frame_data_url: str
    question: str
    annotations: list[dict[str, Any]] = Field(default_factory=list)


async def _youtube_url_from_request(request: Request, form_value: str | None = None) -> str | None:
    if form_value and form_value.strip():
        return form_value.strip()

    query_value = request.query_params.get("youtube_url")
    if query_value and query_value.strip():
        return query_value.strip()

    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        return None

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from None

    youtube_url = payload.get("youtube_url") if isinstance(payload, dict) else None
    if isinstance(youtube_url, str) and youtube_url.strip():
        return youtube_url.strip()
    return None


def _build_segmentation_prompt(question: str, annotations: list[dict[str, Any]]) -> str:
    if annotations:
        return f"Track the operator-referenced object related to: {question}"
    return f"Track the primary object relevant to question: {question}"


async def _retrieve_document_chunks(
    client: httpx.AsyncClient,
    document_ids: list[str],
    question: str,
    *,
    top_k: int = 4,
) -> list[str]:
    if not document_ids:
        return []
    response = await client.post(
        f"{RAGVLM_SERVICE_URL}/documents/retrieve",
        json={"question": question, "document_ids": document_ids, "top_k": top_k},
    )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    chunks = response.json().get("chunks", [])
    return [
        chunk["text"]
        for chunk in chunks
        if isinstance(chunk, dict) and isinstance(chunk.get("text"), str) and chunk["text"]
    ]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/media/ingest")
async def media_ingest(
    request: Request,
    file: UploadFile = File(default=None),
    youtube_url: str | None = Form(default=None),
) -> Any:
    youtube_url = await _youtube_url_from_request(request, youtube_url)
    if not file and not youtube_url:
        raise HTTPException(status_code=400, detail="Provide file or youtube_url")

    try:
        async with httpx.AsyncClient(timeout=MEDIA_INGEST_TIMEOUT_SECONDS) as client:
            if file:
                payload = {"file": (file.filename, await file.read(), file.content_type)}
                response = await client.post(f"{VIDEO_SERVICE_URL}/media/ingest", files=payload)
            else:
                response = await client.post(
                    f"{VIDEO_SERVICE_URL}/media/ingest",
                    json={"youtube_url": youtube_url},
                )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                f"Media ingest timed out after {MEDIA_INGEST_TIMEOUT_SECONDS:g}s while waiting for video-service. "
                "Large YouTube downloads, frame extraction, and first-run Whisper model downloads can take several "
                "minutes. Increase MEDIA_INGEST_TIMEOUT_SECONDS, set WHISPER_ENABLED=false to use fallback "
                "transcripts during ingest, or retry after video-service finishes warming up."
            ),
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    _log_event("media_ingested", source="file" if file else "youtube")
    return response.json()


@app.get("/media/source")
async def media_source(video_id: str, request: Request) -> StreamingResponse:
    headers = {}
    if range_header := request.headers.get("range"):
        headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=None)
    upstream: httpx.Response | None = None
    try:
        upstream = await client.send(
            client.build_request(
                "GET",
                f"{VIDEO_SERVICE_URL}/media/source",
                params={"video_id": video_id},
                headers=headers,
            ),
            stream=True,
        )
        if upstream.status_code >= 400:
            text = await upstream.aread()
            raise HTTPException(status_code=upstream.status_code, detail=text.decode())

        passthrough_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() in {"accept-ranges", "content-length", "content-range", "content-type"}
        }
    except BaseException:
        if upstream is not None:
            await upstream.aclose()
        await client.aclose()
        raise

    async def stream() -> Any:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream(),
        status_code=upstream.status_code,
        headers=passthrough_headers,
        media_type=upstream.headers.get("content-type", "video/mp4"),
    )


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


@app.post("/documents/ingest")
async def document_ingest(
    file: UploadFile = File(...),
    document_id: str | None = None,
) -> Any:
    payload = {"file": (file.filename, await file.read(), file.content_type)}
    params = {"document_id": document_id} if document_id else None
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{RAGVLM_SERVICE_URL}/documents/ingest",
            files=payload,
            params=params,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    _log_event("document_ingested", filename=file.filename)
    return response.json()


@app.post("/documents/retrieve")
async def document_retrieve(payload: DocumentRetrieveRequest) -> Any:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{RAGVLM_SERVICE_URL}/documents/retrieve",
            json=payload.model_dump(),
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    _log_event("document_retrieved", document_count=len(payload.document_ids), question_len=len(payload.question))
    return response.json()


@app.post("/chat/stream")
async def chat_stream(payload: ChatStreamRequest) -> StreamingResponse:
    _log_event("inference_started", session_id=payload.session_id, video_id=payload.video_id, timestamp=payload.timestamp)
    async with httpx.AsyncClient(timeout=60) as client:
        retrieved_chunks = await _retrieve_document_chunks(
            client,
            payload.document_ids,
            payload.question,
        )
    request_body = {
        "question": payload.question,
        "frame_data_url": payload.frame_data_url,
        "annotations": payload.annotations,
        "transcript_segments": [segment.model_dump() for segment in payload.transcript_window.segments],
        "retrieved_chunks": retrieved_chunks,
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
