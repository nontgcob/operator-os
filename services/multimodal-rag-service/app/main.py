from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile

from .answering import Answerer, AnswerResult, build_answerer, excerpt
from .models import (
    AnswerEnvelope,
    AskRequest,
    Citation,
    IngestResponse,
    ModelKnowledgeDisclosure,
    PipelineDescriptor,
    StatusResponse,
)
from .retrieval import PageRetriever, RetrievedPage, build_retriever
from .storage import OriginalPdfStore, PIPELINE_VERSION


DATA_DIR = Path(os.getenv("MULTIMODAL_RAG_DATA_DIR", "data/multimodal-rag"))
MAX_UPLOAD_BYTES = int(os.getenv("MULTIMODAL_RAG_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
RENDER_DPI = int(os.getenv("MULTIMODAL_RAG_RENDER_DPI", "200"))

store = OriginalPdfStore(DATA_DIR, render_dpi=RENDER_DPI)
retriever: PageRetriever = build_retriever(store)
answerer: Answerer = build_answerer(store)

app = FastAPI(
    title="OperatorOS Independent Multimodal RAG Service",
    version=PIPELINE_VERSION,
)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "pipeline": "multimodal_rag",
        "version": PIPELINE_VERSION,
        "retriever": retriever.name,
        "answerer": answerer.name,
        "data_dir": str(DATA_DIR),
    }


@app.post("/documents/ingest", response_model=IngestResponse)
async def ingest_document(
    request: Request,
    file: UploadFile = File(...),
    document_id: str | None = Form(default=None),
) -> IngestResponse:
    if file.content_type not in {None, "application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Only PDF uploads are supported.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds the {MAX_UPLOAD_BYTES}-byte upload limit.",
        )
    try:
        query_document_id = request.query_params.get("document_id")
        if document_id and query_document_id and document_id != query_document_id:
            raise ValueError("Conflicting form and query document_id values.")
        manifest = store.ingest(
            data,
            filename=file.filename or "manual.pdf",
            requested_document_id=document_id or query_document_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IngestResponse(
        document_id=manifest.document_id,
        version=manifest.version,
        filename=manifest.filename,
        status=manifest.status,
        page_count=manifest.page_count,
        rendered_pages=sum(page.image_path is not None for page in manifest.pages),
        warnings=manifest.warnings,
    )


@app.get("/documents/{document_id}/status", response_model=StatusResponse)
async def document_status(document_id: str) -> StatusResponse:
    try:
        manifest = store.get(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found.") from exc
    return StatusResponse(
        document_id=manifest.document_id,
        version=manifest.version,
        filename=manifest.filename,
        status=manifest.status,
        page_count=manifest.page_count,
        rendered_pages=sum(page.image_path is not None for page in manifest.pages),
        warnings=manifest.warnings,
    )


@app.post("/documents/{document_id}/reprocess", response_model=StatusResponse)
async def reprocess_document(document_id: str) -> StatusResponse:
    try:
        manifest = store.reprocess(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StatusResponse(
        document_id=manifest.document_id,
        version=manifest.version,
        filename=manifest.filename,
        status=manifest.status,
        page_count=manifest.page_count,
        rendered_pages=sum(page.image_path is not None for page in manifest.pages),
        warnings=manifest.warnings,
    )


def _sanitize_citations(text: str, count: int) -> tuple[str, set[int]]:
    valid: set[int] = set()

    def replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if 1 <= number <= count:
            valid.add(number)
            return match.group(0)
        return ""

    return re.sub(r"\[C(\d+)\]", replace, text).strip(), valid


def _citation(item: RetrievedPage, index: int) -> Citation:
    return Citation(
        citation_id=f"C{index}",
        source_kind="document_page",
        document_id=item.manifest.document_id,
        document_version=item.manifest.version,
        filename=item.manifest.filename,
        page=item.page.page,
        excerpt=excerpt(item.page.text),
        image_available=item.page.image_path is not None,
    )


def _fallback_answer_from_evidence(evidence: list[RetrievedPage]) -> tuple[str, set[int]]:
    supported = next((item for item in evidence if item.page.text.strip()), None)
    if supported is None:
        supported = evidence[0] if evidence else None
    if supported is None:
        return "", set()
    index = evidence.index(supported) + 1
    source = f"{supported.manifest.filename}, page {supported.page.page}"
    detail = excerpt(supported.page.text, 700) or "The referenced page is visual and should be inspected directly."
    return f"The most relevant retrieved evidence is from {source}: {detail} [C{index}]", {index}


@app.post("/ask", response_model=AnswerEnvelope)
@app.post("/rag/multimodal/answer", response_model=AnswerEnvelope)
async def ask(payload: AskRequest) -> AnswerEnvelope:
    try:
        evidence = retriever.retrieve(
            payload.question,
            payload.document_ids,
            payload.top_k,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Document not found: {exc.args[0]}",
        ) from exc
    try:
        result = await answerer.answer(
            payload.question,
            evidence,
            payload.allow_model_knowledge,
            payload.conversation,
            payload.model,
        )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Multimodal answer provider failed: {type(exc).__name__}",
        ) from exc

    answer_text, cited_numbers = _sanitize_citations(result.text, len(evidence))
    if evidence and not payload.allow_model_knowledge and not cited_numbers:
        answer_text, cited_numbers = _fallback_answer_from_evidence(evidence)
    citations = [
        _citation(item, index)
        for index, item in enumerate(evidence, start=1)
        if index in cited_numbers
    ]
    has_document_evidence = bool(citations)
    if result.model_knowledge_used and has_document_evidence:
        provenance = "mixed"
    elif result.model_knowledge_used:
        provenance = "model_knowledge"
    elif has_document_evidence:
        provenance = "document"
    else:
        provenance = "insufficient"
    status = "insufficient" if provenance == "insufficient" else "complete"
    disclosure = (
        "This answer also uses the model's internal knowledge."
        if result.model_knowledge_used
        else None
    )
    return AnswerEnvelope(
        answer_id=str(uuid.uuid4()),
        status=status,
        text=answer_text,
        provenance=provenance,
        citations=citations,
        model_knowledge=ModelKnowledgeDisclosure(
            used=result.model_knowledge_used,
            disclosure=disclosure,
        ),
        evidence_count=len(evidence),
        pipeline=PipelineDescriptor(
            version=PIPELINE_VERSION,
            retriever=retriever.name,
            answerer=answerer.name,
        ),
        annotations=[],
        tracking_prompt=None,
        tracking_annotations=[],
        error=None,
    )
