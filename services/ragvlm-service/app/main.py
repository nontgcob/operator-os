from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

try:
    from .annotations import normalize_annotations
    from .model_families import DEFAULT_MODEL, model_family_for, model_supports_reasoning
    from .parse_response import DONE_SENTINEL, parse_openrouter_sse_line
    from .prompts import build_prompt
    from .rag.retrieval import extract_text_from_bytes, ingest_document_text, retrieve_chunks
except ImportError:
    from annotations import normalize_annotations
    from model_families import DEFAULT_MODEL, model_family_for, model_supports_reasoning
    from parse_response import DONE_SENTINEL, parse_openrouter_sse_line
    from prompts import build_prompt
    from rag.retrieval import extract_text_from_bytes, ingest_document_text, retrieve_chunks

app = FastAPI(title="OperatorOS RAGVLM Service", version="0.1.0")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:3000")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "OperatorOS")


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class InferRequest(BaseModel):
    question: str
    frame_data_url: str
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    retrieved_chunks: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    conversation: list[dict[str, str]] = Field(default_factory=list)
    model: str = DEFAULT_MODEL


class DocumentRetrieveRequest(BaseModel):
    question: str
    document_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=4, ge=1, le=12)


def _build_prompt(payload: InferRequest) -> str:
    transcript = "\n".join(
        f"[{segment.start:.2f}-{segment.end:.2f}] {segment.text}"
        for segment in payload.transcript_segments
    ) or "No transcript."
    retrieved = list(payload.retrieved_chunks)
    if payload.document_ids:
        retrieved.extend(
            f"{chunk['filename']}#{chunk['index']}: {chunk['text']}"
            for chunk in retrieve_chunks(payload.question, payload.document_ids)
        )
    docs = "\n\n".join(retrieved) if retrieved else "No retrieved document excerpts."
    return build_prompt(
        payload.question,
        normalize_annotations(payload.annotations),
        transcript,
        docs,
        model_family=model_family_for(payload.model),
    )


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
        text = extract_text_from_bytes(file.filename or "document", file.content_type, data)
        return ingest_document_text(
            text,
            filename=file.filename or "document",
            document_id=document_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/documents/retrieve")
async def retrieve_document_chunks(payload: DocumentRetrieveRequest) -> dict[str, Any]:
    return {
        "chunks": retrieve_chunks(
            payload.question,
            payload.document_ids,
            top_k=payload.top_k,
        )
    }


@app.post("/ragvlm/infer")
async def infer(payload: InferRequest) -> StreamingResponse:
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY missing")

    system_prompt = _build_prompt(payload)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(payload.conversation[-12:])
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": payload.frame_data_url}},
                {"type": "text", "text": payload.question},
            ],
        }
    )

    request_body = {
        "model": payload.model,
        "messages": messages,
        "stream": True,
    }
    if model_supports_reasoning(payload.model):
        request_body["reasoning"] = {"effort": "low"}

    async def stream() -> Any:
        async with httpx.AsyncClient(timeout=90) as client:
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
                    raise HTTPException(status_code=response.status_code, detail=text.decode())
                async for line in response.aiter_lines():
                    parsed = parse_openrouter_sse_line(line)
                    if parsed == DONE_SENTINEL:
                        yield "data: [DONE]\n\n"
                        break
                    if parsed:
                        yield f"data: {parsed}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
