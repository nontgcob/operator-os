from __future__ import annotations

import json
import os
import re
from typing import Any
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
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
        converted_manual_path,
        get_converted_manual,
        get_document_status,
        ingest_document_bytes,
        reprocess_document,
        retrieve_chunks,
        process_staged_document,
        stage_document_bytes,
    )
except ImportError:
    from annotations import normalize_annotations
    from model_families import DEFAULT_MODEL, model_family_for, model_supports_reasoning
    from parse_response import DONE_SENTINEL, parse_openrouter_sse_line
    from prompts import build_prompt
    from rag.retrieval import (
        converted_manual_path,
        get_converted_manual,
        get_document_status,
        ingest_document_bytes,
        reprocess_document,
        retrieve_chunks,
        process_staged_document,
        stage_document_bytes,
    )

app = FastAPI(title="OperatorOS RAGVLM Service", version="0.1.0")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:3000")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "OperatorOS")


def _sse(payload: str, event: str | None = None) -> str:
    lines = payload.split("\n")
    prefix = f"event: {event}\n" if event else ""
    return prefix + "".join(f"data: {line}\n" for line in lines) + "\n"


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Evidence(BaseModel):
    citation_id: str
    source_kind: str = "document"
    document_id: str | None = None
    document_version: str | None = None
    filename: str = "document"
    page: int | None = Field(default=None, ge=1)
    chunk_id: str | None = None
    block_id: str | None = None
    section: str | None = None
    excerpt: str = ""
    text: str | None = None
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    citation_status: str = "ready"
    timestamp_start: float | None = None
    timestamp_end: float | None = None
    score: float | None = None

    def context_text(self) -> str:
        return (self.excerpt or self.text or "").strip()


class InferRequest(BaseModel):
    question: str
    video_title: str | None = None
    frame_data_url: str
    annotated_frame_data_url: str | None = None
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    retrieved_chunks: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    conversation: list[dict[str, str]] = Field(default_factory=list)
    model: str = DEFAULT_MODEL


class DocumentRetrieveRequest(BaseModel):
    question: str
    document_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=4, ge=1, le=12)


class TextAnswerRequest(BaseModel):
    question: str = Field(min_length=1)
    document_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=4, ge=1, le=12)
    evidence: list[Evidence] = Field(default_factory=list)
    conversation: list[dict[str, str]] = Field(default_factory=list)
    model: str = DEFAULT_MODEL
    allow_model_knowledge: bool = True


def _build_prompt(payload: InferRequest) -> str:
    transcript = "\n".join(
        f"[{segment.start:.2f}-{segment.end:.2f}] {segment.text}"
        for segment in payload.transcript_segments
    ) or "No transcript."
    retrieved = list(payload.retrieved_chunks)
    retrieved.extend(
        f"[{item.citation_id}] {item.filename}"
        f"{', page ' + str(item.page) if item.page is not None else ''}: {item.context_text()}"
        for item in payload.evidence
        if item.context_text()
    )
    if payload.document_ids:
        retrieved.extend(
            f"[{chunk['citation_id']}] {chunk['filename']}"
            f"{', page ' + str(chunk['page']) if chunk.get('page') is not None else ''}: {chunk['text']}"
            for chunk in retrieve_chunks(payload.question, payload.document_ids)
        )
    docs = "\n\n".join(retrieved) if retrieved else "No retrieved document excerpts."
    return build_prompt(
        payload.question,
        normalize_annotations(payload.annotations),
        transcript,
        docs,
        model_family=model_family_for(payload.model),
        video_title=payload.video_title,
    )


def _evidence_from_chunk(chunk: dict[str, Any]) -> Evidence:
    return Evidence(
        citation_id=str(chunk.get("citation_id") or f"evidence:{chunk.get('chunk_id', uuid4())}"),
        source_kind=str(chunk.get("source_kind") or "document"),
        document_id=chunk.get("document_id"),
        document_version=chunk.get("document_version"),
        filename=str(chunk.get("filename") or "document"),
        page=chunk.get("page"),
        chunk_id=chunk.get("chunk_id"),
        block_id=chunk.get("block_id"),
        section=chunk.get("section"),
        excerpt=str(chunk.get("excerpt") or chunk.get("text") or ""),
        start_char=chunk.get("start_char"),
        end_char=chunk.get("end_char"),
        citation_status=str(chunk.get("citation_status") or "ready"),
        score=chunk.get("score"),
    )


def _text_answer_prompt(
    question: str,
    evidence: list[Evidence],
    *,
    allow_model_knowledge: bool,
) -> str:
    context = "\n\n".join(
        (
            f"[{item.citation_id}]\n"
            f"Source: {item.filename}"
            f"{', page ' + str(item.page) if item.page is not None else ''}\n"
            f"{item.context_text()}"
        )
        for item in evidence
        if item.context_text()
    ) or "No document evidence was retrieved."
    knowledge_rule = (
        "You may use general model knowledge, but set used_model_knowledge=true for any claim not supported by an evidence ID."
        if allow_model_knowledge
        else "Do not use model knowledge for factual claims; say that the evidence is insufficient."
    )
    return (
        "You are the text-based OperatorOS manual assistant. Answer the user's question accurately and concisely. "
        "Use only the exact evidence IDs listed below for citations; never invent an ID. "
        f"{knowledge_rule}\n\n"
        "Return only JSON with this shape: "
        '{"answer":"markdown answer","citation_ids":["exact evidence ID"],'
        '"used_model_knowledge":false,"insufficient":false}.\n\n'
        f"Question:\n{question}\n\nEvidence:\n{context}"
    )


async def _request_text_completion(messages: list[dict[str, Any]], model: str) -> str:
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY missing")
    request_body: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if model_supports_reasoning(model):
        request_body["reasoning"] = {"effort": "low"}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": OPENROUTER_HTTP_REFERER,
                    "X-Title": OPENROUTER_APP_TITLE,
                },
                json=request_body,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"OpenRouter request failed: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"OpenRouter returned HTTP {response.status_code}: {response.text}",
        )
    payload = response.json()
    try:
        return str(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="OpenRouter response did not contain an answer") from exc


def _parse_text_completion(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return {
            "answer": raw.strip(),
            "citation_ids": [],
            "used_model_knowledge": True,
            "insufficient": not bool(raw.strip()),
        }
    if not isinstance(parsed, dict):
        return {
            "answer": raw.strip(),
            "citation_ids": [],
            "used_model_knowledge": True,
            "insufficient": False,
        }
    citation_ids = parsed.get("citation_ids", [])
    return {
        "answer": str(parsed.get("answer") or "").strip(),
        "citation_ids": [str(item) for item in citation_ids] if isinstance(citation_ids, list) else [],
        "used_model_knowledge": bool(parsed.get("used_model_knowledge", False)),
        "insufficient": bool(parsed.get("insufficient", False)),
    }


def _inline_citation_ids(answer: str, evidence_by_id: dict[str, Evidence]) -> list[str]:
    return [
        citation_id
        for citation_id in evidence_by_id
        if f"[{citation_id}]" in answer or citation_id in answer
    ]


def _citation_from_evidence(item: Evidence) -> dict[str, Any]:
    return {
        "citation_id": item.citation_id,
        "source_kind": item.source_kind,
        "document_id": item.document_id,
        "document_version": item.document_version,
        "filename": item.filename,
        "page": item.page,
        "chunk_id": item.chunk_id,
        "block_id": item.block_id,
        "section": item.section,
        "excerpt": item.context_text(),
        "start_char": item.start_char,
        "end_char": item.end_char,
        "citation_status": item.citation_status,
        "timestamp_start": item.timestamp_start,
        "timestamp_end": item.timestamp_end,
    }


def _offline_text_answer(evidence: list[Evidence]) -> dict[str, Any]:
    supported = next((item for item in evidence if item.context_text()), None)
    if supported is None:
        return {
            "answer": "I don't have enough document evidence to answer this question.",
            "citation_ids": [],
            "used_model_knowledge": False,
            "insufficient": True,
        }
    locator = f" on page {supported.page}" if supported.page is not None else ""
    return {
        "answer": (
            f"According to {supported.filename}{locator}: "
            f"{supported.context_text()} [{supported.citation_id}]"
        ),
        "citation_ids": [supported.citation_id],
        "used_model_knowledge": False,
        "insufficient": False,
    }


def _evidence_fallback_text(evidence: list[Evidence]) -> dict[str, Any] | None:
    supported = next((item for item in evidence if item.context_text()), None)
    if supported is None:
        return None
    locator = f" on page {supported.page}" if supported.page is not None else ""
    return {
        "answer": (
            f"The most relevant retrieved evidence is from {supported.filename}{locator}: "
            f"{supported.context_text()} [{supported.citation_id}]"
        ),
        "citation_ids": [supported.citation_id],
        "used_model_knowledge": False,
        "insufficient": False,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/documents/ingest")
async def ingest_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_id: str | None = None,
) -> dict[str, Any]:
    data = await file.read()
    try:
        is_pdf = (file.filename or "").lower().endswith(".pdf") or file.content_type == "application/pdf"
        async_enabled = os.getenv("RAGVLM_ASYNC_PDF_INGEST", "true").lower() == "true"
        if is_pdf and async_enabled:
            resolved_document_id = document_id or uuid4().hex
            staged = stage_document_bytes(
                data,
                filename=file.filename or "document.pdf",
                content_type=file.content_type,
                document_id=resolved_document_id,
            )
            background_tasks.add_task(process_staged_document, resolved_document_id)
            return staged
        return ingest_document_bytes(
            data,
            filename=file.filename or "document",
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
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/documents/{document_id}/converted-text")
async def converted_text(document_id: str) -> dict[str, Any]:
    try:
        return get_converted_manual(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Converted manual not found") from exc


@app.get("/documents/{document_id}/converted-text/download")
async def download_converted_text(document_id: str) -> FileResponse:
    try:
        path = converted_manual_path(document_id)
        artifact = get_converted_manual(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Converted manual not found") from exc
    source_stem = os.path.splitext(str(artifact["source_filename"]))[0] or "manual"
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{source_stem}.converted.md",
    )


@app.post("/documents/retrieve")
async def retrieve_document_chunks(payload: DocumentRetrieveRequest) -> dict[str, Any]:
    return {
        "chunks": retrieve_chunks(
            payload.question,
            payload.document_ids,
            top_k=payload.top_k,
        )
    }


@app.post("/rag/text/answer")
async def text_rag_answer(payload: TextAnswerRequest) -> dict[str, Any]:
    evidence = list(payload.evidence)
    if not evidence and payload.document_ids:
        evidence = [
            _evidence_from_chunk(chunk)
            for chunk in retrieve_chunks(
                payload.question,
                payload.document_ids,
                top_k=payload.top_k,
            )
        ]
    prompt = _text_answer_prompt(
        payload.question,
        evidence,
        allow_model_knowledge=payload.allow_model_knowledge,
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
    messages.extend(payload.conversation[-12:])
    messages.append({"role": "user", "content": payload.question})
    parsed = (
        _parse_text_completion(await _request_text_completion(messages, payload.model))
        if OPENROUTER_API_KEY
        else _offline_text_answer(evidence)
    )

    evidence_by_id = {item.citation_id: item for item in evidence}
    valid_citation_ids = list(
        dict.fromkeys(
            citation_id
            for citation_id in parsed["citation_ids"] + _inline_citation_ids(parsed["answer"], evidence_by_id)
            if citation_id in evidence_by_id
        )
    )
    if (
        payload.evidence or payload.document_ids
    ) and evidence and (parsed["insufficient"] or (not payload.allow_model_knowledge and not valid_citation_ids)):
        fallback = _evidence_fallback_text(evidence)
        if fallback is not None:
            parsed = fallback
            valid_citation_ids = [
                citation_id
                for citation_id in parsed["citation_ids"]
                if citation_id in evidence_by_id
            ]
    citations = [_citation_from_evidence(evidence_by_id[item]) for item in valid_citation_ids]
    used_model_knowledge = bool(parsed["used_model_knowledge"])
    if parsed["insufficient"]:
        provenance = "insufficient"
    elif citations and used_model_knowledge:
        provenance = "mixed"
    elif citations:
        provenance = "document"
    else:
        provenance = "model_knowledge"
    return {
        "answer_id": str(uuid4()),
        "status": "complete",
        "text": parsed["answer"],
        "provenance": provenance,
        "citations": citations,
        "used_model_knowledge": used_model_knowledge,
        "annotations": [],
        "tracking_prompt": "",
        "tracking_annotations": [],
        "error": None,
    }


@app.post("/ragvlm/infer")
async def infer(payload: InferRequest) -> StreamingResponse:
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY missing")

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
    user_content.append({"type": "text", "text": payload.question})
    messages.append(
        {
            "role": "user",
            "content": user_content,
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
        try:
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
