from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import httpx

try:
    from services.common.env import load_env_file
except ImportError:
    load_env_file = None

if load_env_file:
    load_env_file()

DOCUMENT_DIR = Path(os.getenv("RAGVLM_DOCUMENT_DIR", "data/ragvlm/documents"))
INDEX_FILENAME = "index.json"
FILES_DIRNAME = "files"
EMBEDDING_MODEL = os.getenv("RAGVLM_EMBEDDING_MODEL", "openai/text-embedding-3-small")
EMBEDDING_BATCH_SIZE = 32
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 80
INCLUDE_ALL_ATTACHED_THRESHOLD = 20
TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]+")


def configure_document_dir(path: Path) -> None:
    global DOCUMENT_DIR
    DOCUMENT_DIR = path


def _index_path() -> Path:
    return DOCUMENT_DIR / INDEX_FILENAME


def _files_dir() -> Path:
    return DOCUMENT_DIR / FILES_DIRNAME


def _legacy_document_path(document_id: str) -> Path:
    return DOCUMENT_DIR / f"{document_id}.json"


def _ensure_document_dir() -> None:
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    _files_dir().mkdir(parents=True, exist_ok=True)


def extract_text_from_bytes(filename: str, content_type: str | None, data: bytes) -> str:
    lower_filename = filename.lower()
    is_pdf = lower_filename.endswith(".pdf") or content_type == "application/pdf"
    if is_pdf:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF ingestion requires pypdf to be installed") from exc
        reader = PdfReader(BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(page for page in pages if page.strip())

    is_docx = (
        lower_filename.endswith(".docx")
        or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    if is_docx:
        try:
            from docx import Document
        except ImportError as exc:
            raise ValueError("DOCX ingestion requires python-docx to be installed") from exc
        document = Document(BytesIO(data))
        return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())

    return data.decode("utf-8", errors="ignore")


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """Split text with the same character-window strategy used by upstream RAGVLM."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        if end < len(normalized):
            window = normalized[start:end]
            last_break = max(
                window.rfind("\n\n"),
                window.rfind("\n"),
                window.rfind(". "),
            )
            if last_break > chunk_size * 0.4:
                end = start + last_break + (1 if window[last_break] == "\n" else 2)

        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)

    return chunks


def _empty_index() -> dict[str, Any]:
    return {"version": 1, "documents": [], "chunks": []}


def _load_index() -> dict[str, Any]:
    _ensure_document_dir()
    path = _index_path()
    if not path.exists():
        return _migrate_legacy_documents()
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty_index()
    if not isinstance(index, dict):
        return _empty_index()
    index.setdefault("version", 1)
    index.setdefault("documents", [])
    index.setdefault("chunks", [])
    return index


def _save_index(index: dict[str, Any]) -> None:
    _ensure_document_dir()
    _index_path().write_text(json.dumps(index, indent=2), encoding="utf-8")


def _migrate_legacy_documents() -> dict[str, Any]:
    index = _empty_index()
    legacy_paths = [path for path in sorted(DOCUMENT_DIR.glob("*.json")) if path.name != INDEX_FILENAME]
    for path in legacy_paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        document_id = str(record.get("document_id") or path.stem)
        filename = str(record.get("filename") or "document")
        chunks = record.get("chunks", [])
        if not isinstance(chunks, list):
            continue
        index["documents"].append(
            {
                "id": document_id,
                "name": filename,
                "source": "user",
                "created_at": record.get("created_at", time.time()),
                "chunk_count": len(chunks),
            }
        )
        for idx, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            text = str(chunk.get("text") or "")
            index["chunks"].append(
                {
                    "id": str(chunk.get("chunk_id") or f"{document_id}:{idx}"),
                    "document_id": document_id,
                    "filename": filename,
                    "source": "user",
                    "index": int(chunk.get("index", idx)),
                    "text": text,
                    "embedding": _local_embedding(text),
                    "embedding_model": "local-hash-fallback",
                }
            )
    _save_index(index)
    return index


def _default_document_id(filename: str, text: str) -> str:
    digest = hashlib.sha256(f"{filename}\0{text}".encode("utf-8")).hexdigest()
    return digest[:16]


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def _local_embedding(text: str, dimensions: int = 128) -> list[float]:
    """Deterministic fallback for local tests/dev when OpenRouter embeddings are unavailable."""
    vector = [0.0] * dimensions
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _embed_texts_openrouter(texts: list[str]) -> list[list[float]]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return [_local_embedding(text) for text in texts]

    embeddings: list[list[float]] = []
    with httpx.Client(timeout=90) as client:
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + EMBEDDING_BATCH_SIZE]
            response = client.post(
                "https://openrouter.ai/api/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:3000"),
                    "X-Title": os.getenv("OPENROUTER_APP_TITLE", "OperatorOS"),
                },
                json={"model": EMBEDDING_MODEL, "input": batch},
            )
            if response.status_code >= 400:
                raise ValueError(f"Embedding request failed: {response.text}")
            payload = response.json()
            data = payload.get("data", [])
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            embeddings.extend(item["embedding"] for item in ordered if isinstance(item.get("embedding"), list))

    if len(embeddings) != len(texts):
        raise ValueError("Embedding response did not include one vector per input chunk")
    return embeddings


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(left * right for left, right in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _upsert_document(index: dict[str, Any], document: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    document_id = document["id"]
    return {
        "version": 1,
        "documents": [doc for doc in index.get("documents", []) if doc.get("id") != document_id] + [document],
        "chunks": [chunk for chunk in index.get("chunks", []) if chunk.get("document_id") != document_id] + chunks,
    }


def ingest_document_text(
    text: str,
    *,
    filename: str = "document.txt",
    document_id: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
) -> dict[str, Any]:
    clean_text = text.replace("\r\n", "\n").strip()
    if not clean_text:
        raise ValueError("Document contained no extractable text")

    chunks = chunk_text(clean_text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        raise ValueError("Document contained no extractable text")

    resolved_document_id = document_id or _default_document_id(filename, clean_text)
    embeddings = (embedder or _embed_texts_openrouter)(chunks)
    if len(embeddings) != len(chunks):
        raise ValueError("Embedding function must return one vector per chunk")

    document = {
        "id": resolved_document_id,
        "name": filename,
        "source": "user",
        "created_at": time.time(),
        "chunk_count": len(chunks),
    }
    rag_chunks = [
        {
            "id": str(uuid4()),
            "document_id": resolved_document_id,
            "filename": filename,
            "source": "user",
            "index": idx,
            "text": chunk,
            "embedding": embeddings[idx],
            "embedding_model": EMBEDDING_MODEL if os.getenv("OPENROUTER_API_KEY", "").strip() else "local-hash-fallback",
        }
        for idx, chunk in enumerate(chunks)
    ]

    index = _upsert_document(_load_index(), document, rag_chunks)
    _save_index(index)
    (_files_dir() / f"{resolved_document_id}.txt").write_text(clean_text, encoding="utf-8")

    # Keep a per-document JSON snapshot for easier debugging and backwards compatibility.
    _legacy_document_path(resolved_document_id).write_text(
        json.dumps(
            {
                "document_id": resolved_document_id,
                "filename": filename,
                "created_at": document["created_at"],
                "chunk_count": len(chunks),
                "chunks": [
                    {
                        "chunk_id": chunk["id"],
                        "index": chunk["index"],
                        "text": chunk["text"],
                        "embedding": chunk["embedding"],
                    }
                    for chunk in rag_chunks
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "document_id": resolved_document_id,
        "filename": filename,
        "chunk_count": len(chunks),
    }


def _candidate_chunks(index: dict[str, Any], document_ids: list[str] | None) -> list[dict[str, Any]]:
    chunks = [chunk for chunk in index.get("chunks", []) if isinstance(chunk, dict)]
    if not document_ids:
        return chunks
    id_set = set(document_ids)
    return [chunk for chunk in chunks if chunk.get("document_id") in id_set]


def _format_chunk(chunk: dict[str, Any], score: float) -> dict[str, Any]:
    return {
        "document_id": chunk.get("document_id"),
        "filename": chunk.get("filename", "document"),
        "chunk_id": chunk.get("id"),
        "index": chunk.get("index", 0),
        "text": chunk.get("text", ""),
        "score": round(score, 6),
    }


def retrieve_chunks(
    query: str,
    document_ids: list[str] | None = None,
    *,
    top_k: int = 4,
    query_embedder: Callable[[list[str]], list[list[float]]] | None = None,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        return []

    index = _load_index()
    candidates = _candidate_chunks(index, document_ids)
    if not candidates:
        return []

    if document_ids and len(candidates) <= INCLUDE_ALL_ATTACHED_THRESHOLD:
        ordered = sorted(
            candidates,
            key=lambda chunk: (
                str(chunk.get("filename", "")),
                int(chunk.get("index", 0)),
            ),
        )
        return [_format_chunk(chunk, 1.0) for chunk in ordered[:top_k]]

    query_embedding = (query_embedder or _embed_texts_openrouter)([query])[0]
    scored = [
        _format_chunk(
            chunk,
            _cosine_similarity(query_embedding, [float(value) for value in chunk.get("embedding", [])]),
        )
        for chunk in candidates
    ]
    scored.sort(
        key=lambda chunk: (
            -chunk["score"],
            str(chunk["filename"]),
            int(chunk["index"]),
        )
    )
    return scored[:top_k]
