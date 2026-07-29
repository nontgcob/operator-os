from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PageRecord(BaseModel):
    page: int = Field(ge=1)
    text: str = ""
    image_path: str | None = None
    render_status: Literal["rendered", "text_only", "failed"]
    warnings: list[str] = Field(default_factory=list)


class DocumentManifest(BaseModel):
    document_id: str
    version: str
    filename: str
    checksum: str
    status: Literal["processing", "ready", "partial", "failed"]
    page_count: int = Field(ge=0)
    pages: list[PageRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    index_adapter: str
    created_at: str


class Citation(BaseModel):
    citation_id: str
    source_kind: Literal["document_page"]
    document_id: str
    document_version: str
    filename: str
    page: int = Field(ge=1)
    excerpt: str
    image_available: bool
    bounding_box: list[float] | None = None
    region_id: str | None = None


class ModelKnowledgeDisclosure(BaseModel):
    used: bool
    disclosure: str | None = None


class PipelineDescriptor(BaseModel):
    id: Literal["multimodal_rag"] = "multimodal_rag"
    version: str
    retriever: str
    answerer: str


class AnswerEnvelope(BaseModel):
    answer_id: str
    status: Literal["complete", "insufficient"]
    text: str
    provenance: Literal["document", "model_knowledge", "mixed", "insufficient"]
    citations: list[Citation] = Field(default_factory=list)
    model_knowledge: ModelKnowledgeDisclosure
    evidence_count: int = Field(ge=0)
    pipeline: PipelineDescriptor
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    tracking_prompt: str | None = None
    tracking_annotations: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    document_ids: list[str] = Field(min_length=1, max_length=20)
    top_k: int = Field(default=4, ge=1, le=12)
    allow_model_knowledge: bool = True
    conversation: list[dict[str, str]] = Field(default_factory=list, max_length=50)
    model: str | None = None


class IngestResponse(BaseModel):
    document_id: str
    version: str
    filename: str
    status: Literal["ready", "partial"]
    page_count: int
    rendered_pages: int
    warnings: list[str] = Field(default_factory=list)


class StatusResponse(BaseModel):
    document_id: str
    version: str
    filename: str
    status: Literal["processing", "ready", "partial", "failed"]
    page_count: int
    rendered_pages: int
    warnings: list[str] = Field(default_factory=list)
