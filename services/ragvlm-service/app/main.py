from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

try:
    from services.common.env import load_env_file
except ImportError:
    load_env_file = None

if load_env_file:
    load_env_file()

try:
    from .annotations import normalize_annotations
    from .model_families import DEFAULT_MODEL, model_family_for, model_supports_reasoning
    from .parse_response import DONE_SENTINEL, parse_openrouter_sse_line
    from .prompts import build_prompt
    from .rag.retrieval import (
        get_document_file_content_parts,
        get_document_status,
        ingest_document_bytes,
        reprocess_document,
    )
except ImportError:
    from annotations import normalize_annotations
    from model_families import DEFAULT_MODEL, model_family_for, model_supports_reasoning
    from parse_response import DONE_SENTINEL, parse_openrouter_sse_line
    from prompts import build_prompt
    from rag.retrieval import (
        get_document_file_content_parts,
        get_document_status,
        ingest_document_bytes,
        reprocess_document,
    )

app = FastAPI(title="OperatorOS RAGVLM Service", version="0.1.0")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:3000")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "OperatorOS")
OPENROUTER_PDF_ENGINE = os.getenv("OPENROUTER_PDF_ENGINE", "native")


def _sse(payload: str, event: str | None = None) -> str:
    lines = payload.split("\n")
    prefix = f"event: {event}\n" if event else ""
    return prefix + "".join(f"data: {line}\n" for line in lines) + "\n"


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class InferRequest(BaseModel):
    question: str
    video_title: str | None = None
    frame_data_url: str
    annotated_frame_data_url: str | None = None
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    retrieved_chunks: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    conversation: list[dict[str, str]] = Field(default_factory=list)
    model: str = DEFAULT_MODEL


def _build_prompt(payload: InferRequest) -> str:
    transcript = "\n".join(
        f"[{segment.start:.2f}-{segment.end:.2f}] {segment.text}"
        for segment in payload.transcript_segments
    ) or "No transcript."
    docs = (
        "The selected PDF manual(s) are attached to the user message as native PDF file inputs. "
        "Read those attached files directly and ground document-specific claims in them. "
        "If the PDFs do not contain enough evidence, say so plainly."
        if payload.document_ids
        else "No document PDFs are attached."
    )
    return build_prompt(
        payload.question,
        normalize_annotations(payload.annotations),
        transcript,
        docs,
        model_family=model_family_for(payload.model),
        video_title=payload.video_title,
    )


def _pdf_plugins() -> list[dict[str, Any]] | None:
    if not OPENROUTER_PDF_ENGINE:
        return None
    return [
        {
            "id": "file-parser",
            "pdf": {
                "engine": OPENROUTER_PDF_ENGINE,
            },
        }
    ]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/documents/ingest")
async def ingest_document(
    file: UploadFile = File(...),
    document_id: str | None = None,
) -> dict[str, Any]:
    data = await file.read()
    try:
        return ingest_document_bytes(
            data,
            filename=file.filename or "document.pdf",
            content_type=file.content_type,
            document_id=document_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/documents/{document_id}/status")
async def document_status(document_id: str) -> dict[str, Any]:
    try:
        return get_document_status(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc


@app.post("/documents/{document_id}/reprocess")
async def reprocess_uploaded_document(document_id: str) -> dict[str, Any]:
    try:
        return reprocess_document(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc


@app.get("/documents/{document_id}/converted-text")
async def converted_text(document_id: str) -> None:
    raise HTTPException(status_code=410, detail="Converted text artifacts were removed; PDFs are sent directly.")


@app.get("/documents/{document_id}/converted-text/download")
async def download_converted_text(document_id: str) -> None:
    raise HTTPException(status_code=410, detail="Converted text artifacts were removed; PDFs are sent directly.")


@app.post("/documents/retrieve")
async def retrieve_document_chunks() -> None:
    raise HTTPException(status_code=410, detail="Document retrieval was removed; PDFs are sent directly to the VLM.")


@app.post("/rag/text/answer")
async def text_rag_answer() -> None:
    raise HTTPException(status_code=410, detail="Text RAG was removed; PDFs are sent directly to the VLM.")


@app.post("/ragvlm/infer")
async def infer(payload: InferRequest) -> StreamingResponse:
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY missing")

    try:
        document_parts = get_document_file_content_parts(payload.document_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Document not found: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    system_prompt = _build_prompt(payload)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(payload.conversation[-12:])
    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": "Original video frame:"},
        {"type": "image_url", "image_url": {"url": payload.frame_data_url}},
    ]
    if payload.annotated_frame_data_url:
        user_content.extend(
            [
                {"type": "text", "text": "Same frame with user annotations overlaid:"},
                {"type": "image_url", "image_url": {"url": payload.annotated_frame_data_url}},
            ]
        )
    if document_parts:
        user_content.append(
            {
                "type": "text",
                "text": "Attached PDF manual(s). Use these directly for document-grounded claims:",
            }
        )
        user_content.extend(document_parts)
    user_content.append({"type": "text", "text": payload.question})
    messages.append({"role": "user", "content": user_content})

    request_body: dict[str, Any] = {
        "model": payload.model,
        "messages": messages,
        "stream": True,
    }
    plugins = _pdf_plugins() if document_parts else None
    if plugins:
        request_body["plugins"] = plugins
    if model_supports_reasoning(payload.model):
        request_body["reasoning"] = {"effort": "low"}

    async def stream() -> Any:
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                async with client.stream(
                    "POST",
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": OPENROUTER_HTTP_REFERER,
                        "X-Title": OPENROUTER_APP_TITLE,
                    },
                    json=request_body,
                ) as response:
                    if response.status_code >= 400:
                        text = await response.aread()
                        yield _sse(f"OpenRouter returned HTTP {response.status_code}: {text.decode()}", event="error")
                        return
                    async for line in response.aiter_lines():
                        parsed = parse_openrouter_sse_line(line)
                        if parsed == DONE_SENTINEL:
                            yield _sse("[DONE]")
                            break
                        if parsed:
                            yield _sse(parsed)
        except httpx.HTTPError as exc:
            yield _sse(f"OpenRouter stream failed: {exc}", event="error")

    return StreamingResponse(stream(), media_type="text/event-stream")
