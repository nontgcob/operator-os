from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

DOCUMENT_DIR = Path(os.getenv("RAGVLM_DOCUMENT_DIR", "/app/data/ragvlm/documents"))
TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "with",
}


def configure_document_dir(path: Path) -> None:
    global DOCUMENT_DIR
    DOCUMENT_DIR = path


def _document_path(document_id: str) -> Path:
    return DOCUMENT_DIR / f"{document_id}.json"


def _ensure_document_dir() -> None:
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)


def extract_text_from_bytes(filename: str, content_type: str | None, data: bytes) -> str:
    is_pdf = filename.lower().endswith(".pdf") or content_type == "application/pdf"
    if is_pdf:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF ingestion requires pypdf to be installed") from exc
        reader = PdfReader(BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(page for page in pages if page.strip())
    return data.decode("utf-8", errors="ignore")


def chunk_text(text: str, chunk_size: int = 180, overlap: int = 40) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    words = text.split()
    if not words:
        return []
    if len(words) <= chunk_size:
        return [" ".join(words)]

    chunks: list[str] = []
    step = chunk_size - overlap
    for start in range(0, len(words), step):
        chunk_words = words[start : start + chunk_size]
        if not chunk_words:
            break
        chunks.append(" ".join(chunk_words))
        if start + chunk_size >= len(words):
            break
    return chunks


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if token.lower() not in STOPWORDS
    ]


def _score(query: str, chunk: str) -> float:
    query_tokens = _tokens(query)
    chunk_tokens = _tokens(chunk)
    if not query_tokens or not chunk_tokens:
        return 0.0

    query_counts = {token: query_tokens.count(token) for token in set(query_tokens)}
    chunk_counts = {token: chunk_tokens.count(token) for token in set(chunk_tokens)}
    overlap = sum(min(query_counts[token], chunk_counts.get(token, 0)) for token in query_counts)
    if overlap == 0:
        return 0.0
    return overlap / math.sqrt(len(chunk_tokens))


def _default_document_id(filename: str, text: str) -> str:
    digest = hashlib.sha256(f"{filename}\0{text}".encode("utf-8")).hexdigest()
    return digest[:16]


def ingest_document_text(
    text: str,
    *,
    filename: str = "document.txt",
    document_id: str | None = None,
    chunk_size: int = 180,
    overlap: int = 40,
) -> dict[str, Any]:
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Document contained no extractable text")

    chunks = chunk_text(clean_text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        raise ValueError("Document contained no extractable text")

    resolved_document_id = document_id or _default_document_id(filename, clean_text)
    record = {
        "document_id": resolved_document_id,
        "filename": filename,
        "created_at": time.time(),
        "chunk_count": len(chunks),
        "chunks": [
            {
                "chunk_id": f"{resolved_document_id}:{idx}",
                "index": idx,
                "text": chunk,
            }
            for idx, chunk in enumerate(chunks)
        ],
    }
    _ensure_document_dir()
    _document_path(resolved_document_id).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return {
        "document_id": resolved_document_id,
        "filename": filename,
        "chunk_count": len(chunks),
    }


def _load_document(document_id: str) -> dict[str, Any] | None:
    path = _document_path(document_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_document_ids(document_ids: list[str] | None) -> list[str]:
    _ensure_document_dir()
    if document_ids:
        return document_ids
    return [path.stem for path in sorted(DOCUMENT_DIR.glob("*.json"))]


def retrieve_chunks(
    query: str,
    document_ids: list[str] | None = None,
    *,
    top_k: int = 4,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        return []

    candidates: list[dict[str, Any]] = []
    for document_id in _iter_document_ids(document_ids):
        document = _load_document(document_id)
        if not document:
            continue
        for chunk in document.get("chunks", []):
            text = chunk.get("text", "")
            candidates.append(
                {
                    "document_id": document["document_id"],
                    "filename": document.get("filename", "document"),
                    "chunk_id": chunk.get("chunk_id"),
                    "index": chunk.get("index", 0),
                    "text": text,
                    "score": round(_score(query, text), 6),
                }
            )

    candidates.sort(
        key=lambda chunk: (
            -chunk["score"],
            chunk["filename"],
            chunk["index"],
        )
    )
    return candidates[:top_k]
