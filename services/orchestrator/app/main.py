from __future__ import annotations

import json
import hashlib
import os
import secrets
import sqlite3
import time
import asyncio
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

try:
    from services.common.env import load_env_file
except ImportError:
    load_env_file = None

if load_env_file:
    load_env_file()

try:
    from .memory import append_rolling_conversation
except ImportError:
    from memory import append_rolling_conversation

try:
    from .chat_log import ChatLog
except ImportError:
    from chat_log import ChatLog

try:
    from .comparison_store import ComparisonStore
except ImportError:
    from comparison_store import ComparisonStore

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
USE_REDIS_STATE = os.getenv("USE_REDIS_STATE", "false").lower() == "true"
CHAT_ANALYTICS_DB_PATH = os.getenv(
    "CHAT_ANALYTICS_DB_PATH",
    os.getenv("CHAT_LOG_DB_PATH", "./data/chat-logs/chat-analytics.sqlite3"),
)
RAG_COMPARISON_DB_PATH = os.getenv(
    "RAG_COMPARISON_DB_PATH",
    "./data/rag-comparisons/comparisons.sqlite3",
)


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

    def delete(self, key: str) -> int:
        return 1 if self._values.pop(key, None) is not None else 0


def _build_state_client() -> Any:
    if not USE_REDIS_STATE:
        return _MemoryState()
    try:
        from redis import Redis
    except ImportError as exc:
        raise RuntimeError("USE_REDIS_STATE=true requires the redis Python package") from exc
    return Redis.from_url(REDIS_URL, decode_responses=True)


state_client = _build_state_client()
chat_log = ChatLog(CHAT_ANALYTICS_DB_PATH)
comparison_store = ComparisonStore(RAG_COMPARISON_DB_PATH)


def _log_event(event_type: str, **fields: Any) -> None:
    print(json.dumps({"event": event_type, "ts": time.time(), **fields}))


def _record_chat_message(
    *,
    session_id: str,
    exchange_id: str,
    role: str,
    content: str,
    status: str,
    context: dict[str, Any] | None = None,
) -> None:
    try:
        chat_log.append_message(
            session_id=session_id,
            exchange_id=exchange_id,
            role=role,
            content=content,
            status=status,
            context=context,
        )
    except (OSError, sqlite3.Error) as exc:
        # Chat logging must never make inference unavailable.
        _log_event(
            "chat_log_failed",
            session_id=session_id,
            exchange_id=exchange_id,
            error=str(exc),
        )


def _record_analytics_event(
    event_type: str,
    *,
    session_id: str | None = None,
    exchange_id: str | None = None,
    video_id: str | None = None,
    tracking_job_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        chat_log.append_event(
            event_type=event_type,
            session_id=session_id,
            exchange_id=exchange_id,
            video_id=video_id,
            tracking_job_id=tracking_job_id,
            payload=payload,
        )
    except (OSError, sqlite3.Error) as exc:
        _log_event(
            "analytics_log_failed",
            event_type=event_type,
            session_id=session_id,
            error=str(exc),
        )


def _data_url_summary(value: str | None) -> dict[str, Any]:
    if not value:
        return {"present": False}
    return {
        "present": True,
        "bytes": len(value.encode("utf-8")),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def _sse(payload: str, event: str | None = None) -> str:
    lines = payload.split("\n")
    prefix = f"event: {event}\n" if event else ""
    return prefix + "".join(f"data: {line}\n" for line in lines) + "\n"


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
    video_title: str | None = None
    timestamp: float
    frame_data_url: str
    annotated_frame_data_url: str | None = None
    question: str
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    transcript_window: TranscriptWindow
    document_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    additional_notes: str = ""


class DocumentRetrieveRequest(BaseModel):
    question: str
    document_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=4, ge=1, le=12)


class ComparisonStreamRequest(BaseModel):
    session_id: str
    question: str
    document_ids: list[str] = Field(default_factory=list)
    video_id: str | None = None
    video_title: str | None = None
    timestamp: float | None = None
    frame_data_url: str | None = None
    annotated_frame_data_url: str | None = None
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    transcript_window: TranscriptWindow | None = None
    model: str | None = None
    retry_of: str | None = None
    additional_notes: str = ""


class ComparisonRevealRequest(BaseModel):
    selected_label: str


ComparisonStreamRequest.model_rebuild()


class TrackingStartRequest(BaseModel):
    session_id: str
    video_id: str
    timestamp: float
    frame_data_url: str
    question: str
    segmentation_prompt: str | None = None
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    targets: list[dict[str, Any]] = Field(default_factory=list)


class TrackingExportLayer(BaseModel):
    job_id: str
    track_ids: list[str]
    color: str
    label: str = "Tracked object"


class TrackingExportRequest(BaseModel):
    video_id: str
    layers: list[TrackingExportLayer]


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


def _enqueue_tracking_job(job_payload: dict[str, Any]) -> None:
    try:
        from redis import Redis
        from rq import Queue
    except ImportError as exc:
        raise RuntimeError("USE_WORKER_QUEUE=true requires redis and rq Python packages") from exc
    Queue("tracking", connection=Redis.from_url(REDIS_URL)).enqueue(
        "workers.tasks.run_tracking_job",
        job_payload,
    )


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


@app.get("/media/metadata")
async def media_metadata(video_id: str) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{VIDEO_SERVICE_URL}/media/metadata",
            params={"video_id": video_id},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


@app.post("/documents/ingest")
async def document_ingest(
    file: UploadFile = File(...),
    document_id: str | None = None,
) -> Any:
    data = await file.read()
    filename = file.filename or "document.pdf"
    resolved_document_id = document_id or hashlib.sha256(
        filename.encode("utf-8") + b"\0" + data
    ).hexdigest()[:16]
    params = {"document_id": resolved_document_id}

    payload = {"file": (filename, data, file.content_type)}
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            f"{RAGVLM_SERVICE_URL}/documents/ingest",
            files=payload,
            params=params,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    result = dict(response.json())
    result["document_id"] = resolved_document_id
    result.setdefault("filename", filename)
    result["pipelines"] = {"direct_pdf_vlm": {"status": result.get("status", "queryable")}}
    _log_event(
        "document_ingested",
        filename=filename,
        document_id=resolved_document_id,
        document_status=result["pipelines"]["direct_pdf_vlm"]["status"],
    )
    return result


@app.get("/documents/preloaded")
async def preloaded_documents() -> Any:
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.get(f"{RAGVLM_SERVICE_URL}/documents/preloaded")
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


async def _get_pipeline_status(url: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            return {"status": "error", "error": response.text}
        return response.json()
    except httpx.HTTPError as exc:
        return {"status": "error", "error": str(exc)}


def _is_document_queryable(status: dict[str, Any]) -> bool:
    raw_status = status.get("status")
    if raw_status in {"ready", "queryable", "complete", "completed"}:
        return True
    return False


@app.get("/documents/{document_id}/status")
async def document_status(document_id: str) -> dict[str, Any]:
    document_status_payload = await _get_pipeline_status(
        f"{RAGVLM_SERVICE_URL}/documents/{document_id}/status"
    )
    normalized_status = (
        "queryable"
        if _is_document_queryable(document_status_payload)
        else document_status_payload.get("status")
    )
    return {
        "document_id": document_id,
        "status": normalized_status,
        "pipelines": {
            "direct_pdf_vlm": {**document_status_payload, "status": normalized_status},
        },
    }


@app.post("/documents/{document_id}/reprocess")
async def reprocess_document(document_id: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(f"{RAGVLM_SERVICE_URL}/documents/{document_id}/reprocess")
        result = response.json() if response.status_code < 400 else {"status": "error", "error": response.text}
    except httpx.HTTPError as exc:
        result = {"status": "error", "error": str(exc)}
    return {
        "document_id": document_id,
        "pipelines": {"direct_pdf_vlm": result},
    }


async def _proxy_text_artifact(document_id: str, *, download: bool) -> Response:
    suffix = "/download" if download else ""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            f"{RAGVLM_SERVICE_URL}/documents/{document_id}/converted-text{suffix}"
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    headers: dict[str, str] = {}
    if content_disposition := response.headers.get("content-disposition"):
        headers["content-disposition"] = content_disposition
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "text/markdown"),
        headers=headers,
    )


@app.get("/documents/{document_id}/converted-text")
async def converted_text(document_id: str) -> Response:
    return await _proxy_text_artifact(document_id, download=False)


@app.get("/documents/{document_id}/converted-text/download")
async def download_converted_text(document_id: str) -> Response:
    return await _proxy_text_artifact(document_id, download=True)


@app.post("/documents/retrieve")
async def document_retrieve(payload: DocumentRetrieveRequest) -> Any:
    raise HTTPException(status_code=410, detail="Document retrieval was removed; PDFs are sent directly to the VLM.")


def _new_blinded_mapping(session_id: str) -> dict[str, str]:
    counts = comparison_store.mapping_counts(session_id)
    text_left = counts.get("text_left", 0)
    multimodal_left = counts.get("multimodal_left", 0)
    if text_left < multimodal_left:
        return {"A": "text_rag", "B": "multimodal_rag"}
    if multimodal_left < text_left:
        return {"A": "multimodal_rag", "B": "text_rag"}
    if secrets.randbelow(2) == 0:
        return {"A": "text_rag", "B": "multimodal_rag"}
    return {"A": "multimodal_rag", "B": "text_rag"}


def _comparison_answer_payload(answer: Any) -> dict[str, Any]:
    """Normalize an answer while removing fields that could reveal its pipeline."""
    if not isinstance(answer, dict):
        return {
            "answer_id": str(uuid4()),
            "status": "error",
            "text": "",
            "provenance": "insufficient",
            "citations": [],
            "annotations": [],
            "tracking_prompt": "",
            "tracking_annotations": [],
            "tracking_targets": [],
            "error": "Pipeline returned an invalid response.",
        }
    normalized = dict(answer)
    for key in ("pipeline", "pipeline_id", "pipeline_name", "retriever", "retrievers"):
        normalized.pop(key, None)
    normalized.setdefault("answer_id", str(uuid4()))
    raw_status = normalized.get("status", "completed")
    normalized["status"] = (
        "completed" if raw_status in {"complete", "completed", "insufficient"} else raw_status
    )
    normalized.setdefault("text", normalized.pop("answer", ""))
    normalized.setdefault("provenance", "insufficient" if not normalized["text"] else "model_knowledge")
    normalized.setdefault("citations", [])
    normalized.setdefault("annotations", [])
    normalized.setdefault("tracking_prompt", "")
    normalized.setdefault("tracking_annotations", [])
    normalized.setdefault("tracking_targets", [])
    normalized.setdefault("error", None)
    if isinstance(normalized["citations"], list):
        citations: list[dict[str, Any]] = []
        for citation in normalized["citations"]:
            if not isinstance(citation, dict):
                continue
            normalized_citation = {
                key: value
                for key, value in citation.items()
                if key not in {"pipeline", "pipeline_id", "retriever", "retrievers"}
            }
            if "page_number" not in normalized_citation and "page" in normalized_citation:
                normalized_citation["page_number"] = normalized_citation["page"]
            if normalized_citation.get("source_kind") == "document_page":
                normalized_citation["source_kind"] = "document"
            citations.append(normalized_citation)
        normalized["citations"] = citations
    return normalized


async def _request_comparison_pipeline(
    pipeline: str,
    payload: ComparisonStreamRequest,
) -> dict[str, Any]:
    raise RuntimeError("Legacy RAG comparison pipelines were removed.")

    transcript_segments = (
        [segment.model_dump() for segment in payload.transcript_window.segments]
        if payload.transcript_window
        else []
    )
    request_body: dict[str, Any] = {
        "question": payload.question,
        "document_ids": payload.document_ids,
        "top_k": 5,
        "conversation": _load_conversation(payload.session_id),
        "video_title": payload.video_title,
        "timestamp": payload.timestamp,
        "frame_data_url": payload.frame_data_url,
        "annotated_frame_data_url": payload.annotated_frame_data_url,
        "annotations": payload.annotations,
        "transcript_segments": transcript_segments,
        "allow_model_knowledge": False,
        "additional_notes": payload.additional_notes,
    }
    if payload.model:
        request_body["model"] = payload.model

    timeout = _env_positive_float("RAG_COMPARISON_TIMEOUT_SECONDS", 120.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=request_body)
    if response.status_code >= 400:
        raise RuntimeError(f"Pipeline returned HTTP {response.status_code}: {response.text}")
    return _comparison_answer_payload(response.json())


@app.post("/chat/comparisons/stream")
async def comparison_stream(payload: ComparisonStreamRequest) -> StreamingResponse:
    raise HTTPException(
        status_code=410,
        detail="Blind text-vs-multimodal RAG comparison was removed with the legacy RAG pipelines.",
    )

    comparison_id = str(uuid4())
    mapping = _new_blinded_mapping(payload.session_id)
    comparison_store.create(
        comparison_id=comparison_id,
        session_id=payload.session_id,
        question=payload.question,
        mapping=mapping,
        request={
            "document_ids": payload.document_ids,
            "model": payload.model,
            "video_id": payload.video_id,
            "video_timestamp_seconds": payload.timestamp,
        },
        retry_of=payload.retry_of,
    )
    _record_chat_message(
        session_id=payload.session_id,
        exchange_id=comparison_id,
        role="user",
        content=payload.question,
        status="received",
        context={"comparison": True, "document_ids": payload.document_ids, "model": payload.model},
    )

    async def run_label(label: str) -> tuple[str, dict[str, Any]]:
        pipeline = mapping[label]
        try:
            answer = await _request_comparison_pipeline(pipeline, payload)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            answer = _comparison_answer_payload(
                {
                    "status": "error",
                    "text": "",
                    "provenance": "insufficient",
                    "error": str(exc),
                }
            )
        comparison_store.record_answer(comparison_id, label=label, answer=answer)
        return label, answer

    async def stream() -> Any:
        yield _sse(
            json.dumps({"comparison_id": comparison_id}, separators=(",", ":")),
            event="comparison_started",
        )
        tasks = {
            asyncio.create_task(run_label(label)): label
            for label in ("A", "B")
        }
        try:
            for completed in asyncio.as_completed(tasks):
                label, answer = await completed
                if answer["status"] == "completed":
                    if answer.get("text"):
                        yield _sse(
                            json.dumps(
                                {
                                    "comparison_id": comparison_id,
                                    "label": label,
                                    "delta": answer["text"],
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            event="answer_delta",
                        )
                    yield _sse(
                        json.dumps(
                            {
                                "comparison_id": comparison_id,
                                "label": label,
                                "answer": answer,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        event="answer_complete",
                    )
                else:
                    yield _sse(
                        json.dumps(
                            {
                                "comparison_id": comparison_id,
                                "label": label,
                                "error": answer.get("error") or "Pipeline unavailable.",
                                "message": answer.get("error") or "Pipeline unavailable.",
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        event="answer_error",
                    )
            record = comparison_store.get(comparison_id)
            yield _sse(
                json.dumps(
                    {"comparison_id": comparison_id, "status": record["status"]},
                    separators=(",", ":"),
                ),
                event="comparison_complete",
            )
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/chat/comparisons/{comparison_id}/reveal")
async def reveal_comparison(comparison_id: str, payload: ComparisonRevealRequest) -> dict[str, Any]:
    try:
        before = comparison_store.get(comparison_id)
        record = comparison_store.reveal(comparison_id, payload.selected_label)
    except KeyError:
        raise HTTPException(status_code=404, detail="Comparison not found.") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not before["selected_label"]:
        selected_answer = record["answers"].get(payload.selected_label, {})
        _append_conversation(record["session_id"], record["question"], selected_answer.get("text", ""))
        _record_chat_message(
            session_id=record["session_id"],
            exchange_id=comparison_id,
            role="assistant",
            content=selected_answer.get("text", ""),
            status="completed",
            context={
                "comparison": True,
                "selected_label": payload.selected_label,
                "selected_pipeline": record["mapping"][payload.selected_label],
                "mapping": record["mapping"],
                "answers": record["answers"],
            },
        )
    return {
        "comparison_id": comparison_id,
        "selected_label": record["selected_label"],
        "mapping": record["mapping"],
    }


@app.post("/chat/stream")
async def chat_stream(payload: ChatStreamRequest) -> StreamingResponse:
    exchange_id = str(uuid4())
    chat_context = {
        "video_id": payload.video_id,
        "video_title": payload.video_title,
        "video_timestamp_seconds": payload.timestamp,
        "model": payload.model,
        "document_ids": payload.document_ids,
        "annotation_count": len(payload.annotations),
        "annotations": payload.annotations,
        "transcript_window": payload.transcript_window.model_dump(),
        "frame_data_url": _data_url_summary(payload.frame_data_url),
        "annotated_frame_data_url": _data_url_summary(payload.annotated_frame_data_url),
        "annotated_snapshot_sent": payload.annotated_frame_data_url is not None,
        "additional_notes": payload.additional_notes,
    }
    _record_chat_message(
        session_id=payload.session_id,
        exchange_id=exchange_id,
        role="user",
        content=payload.question,
        status="received",
        context=chat_context,
    )
    _log_event("inference_started", session_id=payload.session_id, video_id=payload.video_id, timestamp=payload.timestamp)
    request_body = {
        "question": payload.question,
        "video_title": payload.video_title,
        "frame_data_url": payload.frame_data_url,
        "annotated_frame_data_url": payload.annotated_frame_data_url,
        "annotations": payload.annotations,
        "transcript_segments": [segment.model_dump() for segment in payload.transcript_window.segments],
        "document_ids": payload.document_ids,
        "conversation": _load_conversation(payload.session_id),
        "additional_notes": payload.additional_notes,
    }
    if payload.model:
        request_body["model"] = payload.model

    async def stream() -> Any:
        full_text = ""
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{RAGVLM_SERVICE_URL}/ragvlm/infer",
                    json=request_body,
                ) as response:
                    if response.status_code >= 400:
                        text = await response.aread()
                        message = f"RAGVLM returned HTTP {response.status_code}: {text.decode()}"
                        _record_chat_message(
                            session_id=payload.session_id,
                            exchange_id=exchange_id,
                            role="assistant",
                            content=message,
                            status="error",
                            context={"model": payload.model},
                        )
                        yield _sse(message, event="error")
                        return

                    current_event = "message"
                    async for line in response.aiter_lines():
                        if line.startswith("event: "):
                            current_event = line[7:]
                            continue
                        if not line.startswith("data: "):
                            continue
                        chunk = line[6:]
                        if current_event == "error":
                            _record_chat_message(
                                session_id=payload.session_id,
                                exchange_id=exchange_id,
                                role="assistant",
                                content=chunk,
                                status="error",
                                context={"model": payload.model},
                            )
                            yield _sse(chunk, event="error")
                            return
                        if chunk == "[DONE]":
                            break
                        full_text += chunk
                        yield _sse(chunk)
                    _append_conversation(payload.session_id, payload.question, full_text)
                    _record_chat_message(
                        session_id=payload.session_id,
                        exchange_id=exchange_id,
                        role="assistant",
                        content=full_text,
                        status="completed",
                        context={"model": payload.model},
                    )
                    _log_event("inference_completed", session_id=payload.session_id, answer_len=len(full_text))
                    yield _sse("[DONE]")
        except asyncio.CancelledError:
            _record_chat_message(
                session_id=payload.session_id,
                exchange_id=exchange_id,
                role="assistant",
                content=full_text,
                status="cancelled",
                context={"model": payload.model},
            )
            raise
        except httpx.HTTPError as exc:
            message = f"RAGVLM stream failed: {exc}"
            _record_chat_message(
                session_id=payload.session_id,
                exchange_id=exchange_id,
                role="assistant",
                content=message,
                status="error",
                context={"model": payload.model},
            )
            yield _sse(message, event="error")

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/tracking/start")
async def tracking_start(payload: TrackingStartRequest) -> dict[str, str]:
    tracking_job_id = str(uuid4())
    use_worker = os.getenv("USE_WORKER_QUEUE", "false").lower() == "true"
    segmentation_prompt = payload.segmentation_prompt or _build_segmentation_prompt(payload.question, payload.annotations)
    job_payload = {**payload.model_dump(), "tracking_job_id": tracking_job_id, "segmentation_prompt": segmentation_prompt}
    if use_worker:
        state_client.setex(
            f"tracking:{tracking_job_id}",
            3600,
            json.dumps(
                {
                    "tracking_job_id": tracking_job_id,
                    "done": False,
                    "progress": 0,
                    "stage": "queued",
                    "target_progress": [
                        {
                            "target_id": target.get("id") or f"target-{index + 1}",
                            "label": target.get("label") or "Tracked object",
                            "progress": 0,
                            "stage": "queued",
                            "color": target.get("color") or "#67A552",
                        }
                        for index, target in enumerate(payload.targets)
                    ],
                    "overlays": [],
                }
            ),
        )
        _enqueue_tracking_job(job_payload)
    else:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{SAM3_SERVICE_URL}/tracking/start",
                json=job_payload,
            )
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
    _record_analytics_event(
        "tracking_started",
        session_id=payload.session_id,
        video_id=payload.video_id,
        tracking_job_id=tracking_job_id,
        payload={
            "video_timestamp_seconds": payload.timestamp,
            "question": payload.question,
            "segmentation_prompt": segmentation_prompt,
            "annotation_count": len(payload.annotations),
            "annotations": payload.annotations,
            "targets": payload.targets,
            "frame_data_url": _data_url_summary(payload.frame_data_url),
            "use_worker_queue": use_worker,
        },
    )
    _log_event("tracking_started", tracking_job_id=tracking_job_id, session_id=payload.session_id)
    return {"tracking_job_id": tracking_job_id}


@app.get("/tracking/events/{tracking_job_id}")
async def tracking_events(tracking_job_id: str) -> StreamingResponse:
    use_worker = os.getenv("USE_WORKER_QUEUE", "false").lower() == "true"
    if not use_worker:
        async def proxy_stream() -> Any:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET",
                    f"{SAM3_SERVICE_URL}/tracking/events/{tracking_job_id}",
                ) as response:
                    if response.status_code >= 400:
                        text = await response.aread()
                        yield f"event: error\ndata: {text.decode()}\n\n"
                        return
                    async for chunk in response.aiter_text():
                        yield chunk

        return StreamingResponse(proxy_stream(), media_type="text/event-stream")

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
        yield "data: {\"done\": true, \"overlays\": []}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/tracking/cancel/{tracking_job_id}")
async def cancel_tracking(tracking_job_id: str) -> Response:
    use_worker = os.getenv("USE_WORKER_QUEUE", "false").lower() == "true"
    async with httpx.AsyncClient(timeout=30) as client:
        upstream = await client.post(f"{SAM3_SERVICE_URL}/tracking/cancel/{tracking_job_id}")
    if upstream.status_code >= 400 and not (use_worker and upstream.status_code == 404):
        raise HTTPException(status_code=upstream.status_code, detail=upstream.text)
    response_content = upstream.content
    if use_worker:
        current_raw = state_client.get(f"tracking:{tracking_job_id}")
        if not current_raw:
            raise HTTPException(status_code=404, detail="Tracking job was not found or expired")
        current = json.loads(current_raw)
        cancelled = {
            "tracking_job_id": tracking_job_id,
            "done": True,
            "cancelled": True,
            "progress": 0,
            "stage": "cancelled",
            "target_progress": [
                {**target, "progress": 0, "stage": "cancelled"}
                for target in current.get("target_progress", [])
            ],
            "overlays": [],
            "backend": current.get("backend") or "worker",
        }
        state_client.setex(f"tracking:{tracking_job_id}", 3600, json.dumps(cancelled))
        response_content = json.dumps(cancelled).encode()
    _record_analytics_event("tracking_cancelled", tracking_job_id=tracking_job_id)
    _log_event("tracking_cancelled", tracking_job_id=tracking_job_id)
    return Response(content=response_content, media_type="application/json")


async def _proxy_tracking_video(
    upstream_path: str,
    request: Request | None = None,
    *,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
) -> StreamingResponse:
    headers = {}
    if request is not None and (range_header := request.headers.get("range")):
        headers["Range"] = range_header
    client = httpx.AsyncClient(timeout=None)
    upstream = await client.send(
        client.build_request(
            method,
            f"{SAM3_SERVICE_URL}{upstream_path}",
            headers=headers,
            json=json_body,
        ),
        stream=True,
    )
    if upstream.status_code >= 400:
        detail = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=upstream.status_code, detail=detail.decode())

    async def stream_video() -> Any:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    passthrough = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() in {"accept-ranges", "content-length", "content-range", "content-disposition"}
    }
    return StreamingResponse(
        stream_video(),
        status_code=upstream.status_code,
        headers=passthrough,
        media_type="video/mp4",
    )


@app.get("/tracking/video/{tracking_job_id}")
async def tracking_video(tracking_job_id: str, request: Request) -> StreamingResponse:
    return await _proxy_tracking_video(f"/tracking/video/{tracking_job_id}", request)


@app.get("/tracking/clean-video/{tracking_job_id}")
async def clean_tracking_video(tracking_job_id: str, request: Request) -> StreamingResponse:
    return await _proxy_tracking_video(f"/tracking/clean-video/{tracking_job_id}", request)


@app.post("/tracking/export")
async def export_tracking_video(payload: TrackingExportRequest) -> StreamingResponse:
    return await _proxy_tracking_video(
        "/tracking/export",
        method="POST",
        json_body=payload.model_dump(),
    )


@app.get("/tracking/overlays/{tracking_job_id}")
async def tracking_overlays(tracking_job_id: str) -> Response:
    async with httpx.AsyncClient(timeout=30) as client:
        upstream = await client.get(f"{SAM3_SERVICE_URL}/tracking/overlays/{tracking_job_id}")
    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail=upstream.text)
    return Response(content=upstream.content, media_type="application/json")


def _load_conversation(session_id: str) -> list[dict[str, str]]:
    raw = state_client.get(f"conversation:{session_id}")
    if not raw:
        return []
    return json.loads(raw)


def _append_conversation(session_id: str, question: str, answer: str) -> None:
    current = _load_conversation(session_id)
    updated = append_rolling_conversation(current, question, answer, max_messages=12)
    state_client.setex(f"conversation:{session_id}", 3600, json.dumps(updated))


@app.delete("/chat/session/{session_id}")
async def clear_chat_session(session_id: str) -> dict[str, Any]:
    deleted = state_client.delete(f"conversation:{session_id}")
    _record_analytics_event(
        "chat_memory_cleared",
        session_id=session_id,
        payload={"deleted": int(deleted or 0)},
    )
    return {"session_id": session_id, "cleared": True}
