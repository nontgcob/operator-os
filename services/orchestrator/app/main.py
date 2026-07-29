from __future__ import annotations

import json
import hashlib
import os
import secrets
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
MULTIMODAL_RAG_SERVICE_URL = os.getenv("MULTIMODAL_RAG_SERVICE_URL", "http://localhost:8004")
VIDEO_SERVICE_URL = os.getenv("VIDEO_SERVICE_URL", "http://localhost:8002")
SAM3_SERVICE_URL = os.getenv("SAM3_SERVICE_URL", "http://localhost:8003")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
USE_REDIS_STATE = os.getenv("USE_REDIS_STATE", "false").lower() == "true"
CHAT_LOG_PATH = os.getenv("CHAT_LOG_PATH", "./data/chat-logs/chat-conversations.jsonl")
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


def _build_state_client() -> Any:
    if not USE_REDIS_STATE:
        return _MemoryState()
    try:
        from redis import Redis
    except ImportError as exc:
        raise RuntimeError("USE_REDIS_STATE=true requires the redis Python package") from exc
    return Redis.from_url(REDIS_URL, decode_responses=True)


state_client = _build_state_client()
chat_log = ChatLog(CHAT_LOG_PATH)
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
    except OSError as exc:
        # Chat logging must never make inference unavailable.
        _log_event(
            "chat_log_failed",
            session_id=session_id,
            exchange_id=exchange_id,
            error=str(exc),
        )


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

    async def ingest_into(url: str) -> httpx.Response:
        payload = {"file": (filename, data, file.content_type)}
        async with httpx.AsyncClient(timeout=300) as client:
            return await client.post(url, files=payload, params=params)

    text_task = asyncio.create_task(ingest_into(f"{RAGVLM_SERVICE_URL}/documents/ingest"))
    multimodal_task = asyncio.create_task(
        ingest_into(f"{MULTIMODAL_RAG_SERVICE_URL}/documents/ingest")
    )
    text_response, multimodal_result = await asyncio.gather(
        text_task,
        multimodal_task,
        return_exceptions=True,
    )
    if isinstance(text_response, Exception):
        raise HTTPException(status_code=502, detail=f"Text RAG ingestion failed: {text_response}")
    if text_response.status_code >= 400:
        raise HTTPException(status_code=text_response.status_code, detail=text_response.text)

    result = dict(text_response.json())
    result["document_id"] = resolved_document_id
    result.setdefault("filename", filename)
    result["pipelines"] = {
        "text_rag": {
            "status": result.get("status", "queryable"),
        },
        "multimodal_rag": {"status": "processing"},
    }
    if isinstance(multimodal_result, Exception):
        result["pipelines"]["multimodal_rag"] = {
            "status": "error",
            "error": str(multimodal_result),
        }
    elif multimodal_result.status_code >= 400:
        result["pipelines"]["multimodal_rag"] = {
            "status": "error",
            "error": multimodal_result.text,
        }
    else:
        multimodal_payload = multimodal_result.json()
        result["pipelines"]["multimodal_rag"] = {
            "status": multimodal_payload.get("status", "queryable"),
            **{
                key: value
                for key, value in multimodal_payload.items()
                if key not in {"document_id", "filename", "status"}
            },
        }
    _log_event(
        "document_ingested",
        filename=filename,
        document_id=resolved_document_id,
        multimodal_status=result["pipelines"]["multimodal_rag"]["status"],
    )
    return result


async def _get_pipeline_status(url: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            return {"status": "error", "error": response.text}
        return response.json()
    except httpx.HTTPError as exc:
        return {"status": "error", "error": str(exc)}


def _is_text_rag_queryable(status: dict[str, Any]) -> bool:
    raw_status = status.get("status")
    if raw_status in {"ready", "queryable", "complete", "completed"}:
        return True
    return raw_status == "partial" and int(status.get("chunk_count") or 0) > 0


def _is_multimodal_rag_queryable(status: dict[str, Any]) -> bool:
    raw_status = status.get("status")
    if raw_status in {"ready", "queryable", "complete", "completed"}:
        return True
    if raw_status != "partial":
        return False
    return int(status.get("page_count") or 0) > 0 and bool(status.get("version"))


@app.get("/documents/{document_id}/status")
async def document_status(document_id: str) -> dict[str, Any]:
    text_status, multimodal_status = await asyncio.gather(
        _get_pipeline_status(f"{RAGVLM_SERVICE_URL}/documents/{document_id}/status"),
        _get_pipeline_status(f"{MULTIMODAL_RAG_SERVICE_URL}/documents/{document_id}/status"),
    )
    normalized_statuses = {
        "text_rag": (
            "queryable"
            if _is_text_rag_queryable(text_status)
            else text_status.get("status")
        ),
        "multimodal_rag": (
            "queryable"
            if _is_multimodal_rag_queryable(multimodal_status)
            else multimodal_status.get("status")
        ),
    }
    return {
        "document_id": document_id,
        "status": (
            "queryable"
            if set(normalized_statuses.values()) == {"queryable"}
            else "processing"
            if "processing" in normalized_statuses.values()
            else "partial"
        ),
        "pipelines": {
            "text_rag": {**text_status, "status": normalized_statuses["text_rag"]},
            "multimodal_rag": {
                **multimodal_status,
                "status": normalized_statuses["multimodal_rag"],
            },
        },
    }


@app.post("/documents/{document_id}/reprocess")
async def reprocess_document(document_id: str) -> dict[str, Any]:
    async def reprocess(url: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.post(url)
            if response.status_code >= 400:
                return {"status": "error", "error": response.text}
            return response.json()
        except httpx.HTTPError as exc:
            return {"status": "error", "error": str(exc)}

    text_result, multimodal_result = await asyncio.gather(
        reprocess(f"{RAGVLM_SERVICE_URL}/documents/{document_id}/reprocess"),
        reprocess(f"{MULTIMODAL_RAG_SERVICE_URL}/documents/{document_id}/reprocess"),
    )
    return {
        "document_id": document_id,
        "pipelines": {
            "text_rag": text_result,
            "multimodal_rag": multimodal_result,
        },
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
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{RAGVLM_SERVICE_URL}/documents/retrieve",
            json=payload.model_dump(),
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    _log_event("document_retrieved", document_count=len(payload.document_ids), question_len=len(payload.question))
    return response.json()


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
    if pipeline == "text_rag":
        url = f"{RAGVLM_SERVICE_URL}/rag/text/answer"
    elif pipeline == "multimodal_rag":
        url = f"{MULTIMODAL_RAG_SERVICE_URL}/rag/multimodal/answer"
    else:
        raise ValueError(f"Unknown comparison pipeline: {pipeline}")

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
    if not payload.document_ids:
        raise HTTPException(status_code=400, detail="Select at least one queryable PDF for comparison mode.")

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
        "annotated_snapshot_sent": payload.annotated_frame_data_url is not None,
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
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            retrieved_chunks = await _retrieve_document_chunks(
                client,
                payload.document_ids,
                payload.question,
            )
    except HTTPException as exc:
        message = f"Document retrieval failed: {exc.detail}"
        _record_chat_message(
            session_id=payload.session_id,
            exchange_id=exchange_id,
            role="assistant",
            content=message,
            status="error",
            context={"model": payload.model},
        )
        raise
    except httpx.HTTPError as exc:
        message = f"Document retrieval failed: {exc}"
        _record_chat_message(
            session_id=payload.session_id,
            exchange_id=exchange_id,
            role="assistant",
            content=message,
            status="error",
            context={"model": payload.model},
        )
        raise HTTPException(status_code=502, detail=message) from exc
    request_body = {
        "question": payload.question,
        "video_title": payload.video_title,
        "frame_data_url": payload.frame_data_url,
        "annotated_frame_data_url": payload.annotated_frame_data_url,
        "annotations": payload.annotations,
        "transcript_segments": [segment.model_dump() for segment in payload.transcript_window.segments],
        "retrieved_chunks": retrieved_chunks,
        "conversation": _load_conversation(payload.session_id),
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
            json.dumps({"tracking_job_id": tracking_job_id, "done": False, "progress": 0, "overlays": []}),
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
        for _ in range(120):
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


@app.get("/tracking/video/{tracking_job_id}")
async def tracking_video(tracking_job_id: str, request: Request) -> StreamingResponse:
    headers = {}
    if range_header := request.headers.get("range"):
        headers["Range"] = range_header
    client = httpx.AsyncClient(timeout=None)
    upstream = await client.send(
        client.build_request(
            "GET",
            f"{SAM3_SERVICE_URL}/tracking/video/{tracking_job_id}",
            headers=headers,
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
        if key.lower() in {"accept-ranges", "content-length", "content-range"}
    }
    return StreamingResponse(
        stream_video(),
        status_code=upstream.status_code,
        headers=passthrough,
        media_type="video/mp4",
    )


def _load_conversation(session_id: str) -> list[dict[str, str]]:
    raw = state_client.get(f"conversation:{session_id}")
    if not raw:
        return []
    return json.loads(raw)


def _append_conversation(session_id: str, question: str, answer: str) -> None:
    current = _load_conversation(session_id)
    updated = append_rolling_conversation(current, question, answer, max_messages=12)
    state_client.setex(f"conversation:{session_id}", 3600, json.dumps(updated))
