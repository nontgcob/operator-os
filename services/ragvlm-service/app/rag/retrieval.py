from __future__ import annotations

import hashlib
import base64
import json
import math
import mimetypes
import os
import re
import time
from io import BytesIO
from pathlib import Path
from threading import RLock
from typing import Any, Callable

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
ORIGINALS_DIRNAME = "originals"
ARTIFACTS_DIRNAME = "artifacts"
EMBEDDING_MODEL = os.getenv("RAGVLM_EMBEDDING_MODEL", "openai/text-embedding-3-small")
CONVERSION_MODEL = os.getenv("RAGVLM_CONVERSION_MODEL", "qwen/qwen3-vl-8b-instruct")
EMBEDDING_BATCH_SIZE = 32
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 80
TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]+")
DOCUMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
INDEX_LOCK = RLock()


def configure_document_dir(path: Path) -> None:
    global DOCUMENT_DIR
    DOCUMENT_DIR = path


def _index_path() -> Path:
    return DOCUMENT_DIR / INDEX_FILENAME


def _files_dir() -> Path:
    return DOCUMENT_DIR / FILES_DIRNAME


def _originals_dir() -> Path:
    return DOCUMENT_DIR / ORIGINALS_DIRNAME


def _artifacts_dir() -> Path:
    return DOCUMENT_DIR / ARTIFACTS_DIRNAME


def _artifact_dir(document_id: str) -> Path:
    return _artifacts_dir() / document_id


def _legacy_document_path(document_id: str) -> Path:
    return DOCUMENT_DIR / f"{document_id}.json"


def _ensure_document_dir() -> None:
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    _files_dir().mkdir(parents=True, exist_ok=True)
    _originals_dir().mkdir(parents=True, exist_ok=True)
    _artifacts_dir().mkdir(parents=True, exist_ok=True)


def extract_pages_from_bytes(filename: str, content_type: str | None, data: bytes) -> list[dict[str, Any]]:
    """Extract text without discarding source-page provenance.

    Non-paginated formats are represented as a single page with ``page=None``.
    This prevents the UI from presenting a synthetic page number for TXT,
    Markdown, or DOCX documents.
    """
    lower_filename = filename.lower()
    is_pdf = lower_filename.endswith(".pdf") or content_type == "application/pdf"
    if is_pdf:
        conversion_enabled = (
            os.getenv("RAGVLM_VLM_CONVERSION_ENABLED", "true").lower() == "true"
            and bool(os.getenv("OPENROUTER_API_KEY", "").strip())
        )
        if conversion_enabled:
            try:
                import fitz  # type: ignore[import-not-found]

                pdf = fitz.open(stream=data, filetype="pdf")
                rendered_pages: list[dict[str, Any]] = []
                scale = float(os.getenv("RAGVLM_CONVERSION_RENDER_DPI", "160")) / 72.0
                for page_number in range(1, pdf.page_count + 1):
                    page = pdf.load_page(page_number - 1)
                    pixmap = page.get_pixmap(
                        matrix=fitz.Matrix(scale, scale),
                        alpha=False,
                    )
                    image_data = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
                    rendered_pages.append(
                        {
                            "page": page_number,
                            "text": page.get_text("text") or "",
                            "source_kind": "pdf_page",
                            "image_data_url": f"data:image/png;base64,{image_data}",
                        }
                    )
                pdf.close()
                return rendered_pages
            except (ImportError, RuntimeError, ValueError):
                # Native pypdf extraction below remains a safe conversion fallback.
                pass
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF ingestion requires pypdf to be installed") from exc
        reader = PdfReader(BytesIO(data))
        return [
            {
                "page": page_number,
                "text": page.extract_text() or "",
                "source_kind": "pdf_page",
            }
            for page_number, page in enumerate(reader.pages, start=1)
        ]

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
        return [
            {
                "page": None,
                "text": "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()),
                "source_kind": "document",
            }
        ]

    return [{"page": None, "text": data.decode("utf-8", errors="ignore"), "source_kind": "document"}]


def _convert_page_with_vlm(page: dict[str, Any]) -> str:
    image_data_url = page.get("image_data_url")
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key or not isinstance(image_data_url, str) or not image_data_url:
        return str(page.get("text") or "")
    native_text = str(page.get("text") or "")
    prompt = (
        "Convert this single user-manual page into faithful Markdown for retrieval. "
        "Preserve headings, ordered steps, warnings, identifiers, values, units, tables, "
        "captions, and visible labels. Briefly describe meaningful diagrams/screenshots and "
        "spatial relationships that are needed to follow the instructions. Do not add facts, "
        "procedures, or product knowledge that are not visible on this page. Mark unreadable "
        "content as [unclear]. Return Markdown only.\n\n"
        f"Native PDF text (may be incomplete or out of order):\n{native_text}"
    )
    with httpx.Client(timeout=120) as client:
        response = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:3000"),
                "X-Title": os.getenv("OPENROUTER_APP_TITLE", "OperatorOS"),
            },
            json={
                "model": CONVERSION_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_data_url}},
                        ],
                    }
                ],
                "stream": False,
            },
        )
    if response.status_code >= 400:
        raise ValueError(f"page conversion failed with HTTP {response.status_code}")
    payload = response.json()
    converted = str(payload["choices"][0]["message"]["content"]).strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*(.*?)\s*```", converted, re.DOTALL | re.IGNORECASE)
    return fenced.group(1).strip() if fenced else converted


def extract_text_from_bytes(filename: str, content_type: str | None, data: bytes) -> str:
    """Backward-compatible flattened text extraction."""
    return "\n\n".join(
        str(page.get("text") or "")
        for page in extract_pages_from_bytes(filename, content_type, data)
        if str(page.get("text") or "").strip()
    )


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """Split text with the same character-window strategy used by upstream RAGVLM."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []

    return [chunk["text"] for chunk in _chunk_text_with_offsets(normalized, chunk_size, overlap)]


def _chunk_text_with_offsets(text: str, chunk_size: int, overlap: int) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            window = text[start:end]
            last_break = max(
                window.rfind("\n\n"),
                window.rfind("\n"),
                window.rfind(". "),
            )
            if last_break > chunk_size * 0.4:
                end = start + last_break + (1 if window[last_break] == "\n" else 2)

        raw_chunk = text[start:end]
        left_trim = len(raw_chunk) - len(raw_chunk.lstrip())
        chunk = raw_chunk.strip()
        if chunk:
            chunks.append(
                {
                    "text": chunk,
                    "start_char": start + left_trim,
                    "end_char": start + left_trim + len(chunk),
                }
            )

        if end >= len(text):
            break
        start = max(end - overlap, start + 1)

    return chunks


def _empty_index() -> dict[str, Any]:
    return {"version": 2, "documents": [], "chunks": []}


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
    temporary = _index_path().with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, indent=2), encoding="utf-8")
    temporary.replace(_index_path())


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
                "processing_status": "legacy",
                "citation_status": "page_unavailable",
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
                    "page": None,
                    "start_char": None,
                    "end_char": None,
                    "citation_status": "page_unavailable",
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
        "version": 2,
        "documents": [doc for doc in index.get("documents", []) if doc.get("id") != document_id] + [document],
        "chunks": [chunk for chunk in index.get("chunks", []) if chunk.get("document_id") != document_id] + chunks,
    }


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _safe_original_suffix(filename: str, content_type: str | None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix and re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        return suffix
    guessed = mimetypes.guess_extension(content_type or "")
    return guessed if guessed and re.fullmatch(r"\.[a-z0-9]{1,10}", guessed) else ".bin"


def _find_original_path(document_id: str) -> Path | None:
    matches = sorted(_originals_dir().glob(f"{document_id}.*"))
    return matches[0] if matches else None


def _validated_document_id(document_id: str) -> str:
    if not DOCUMENT_ID_RE.fullmatch(document_id):
        raise ValueError(
            "document_id must start with an alphanumeric character and contain only "
            "letters, numbers, '.', '_' or '-'."
        )
    return document_id


def stage_document_bytes(
    data: bytes,
    *,
    filename: str,
    content_type: str | None,
    document_id: str,
) -> dict[str, Any]:
    """Persist an immutable upload and processing record before background conversion."""
    _ensure_document_dir()
    resolved_document_id = _validated_document_id(document_id)
    checksum = hashlib.sha256(data).hexdigest()
    suffix = _safe_original_suffix(filename, content_type)
    original_path = _originals_dir() / f"{resolved_document_id}{suffix}"
    temporary = original_path.with_suffix(original_path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(original_path)
    staged = {
        "document_id": resolved_document_id,
        "document_version": checksum,
        "filename": filename,
        "content_type": content_type,
        "original_path": str(original_path),
        "status": "processing",
        "chunk_count": 0,
        "citation_status": "processing",
    }
    artifact_dir = _artifact_dir(resolved_document_id)
    _atomic_write_text(artifact_dir / "staged.json", json.dumps(staged, indent=2))
    _atomic_write_text(
        artifact_dir / "status.json",
        json.dumps(
            {
                "document_id": resolved_document_id,
                "status": "processing",
                "page_count": 0,
                "ready_pages": 0,
                "partial_pages": 0,
                "unavailable_pages": 0,
                "warnings": [],
            },
            indent=2,
        ),
    )
    return staged


def process_staged_document(document_id: str) -> dict[str, Any]:
    staged_path = _artifact_dir(_validated_document_id(document_id)) / "staged.json"
    if not staged_path.is_file():
        raise KeyError(document_id)
    staged = json.loads(staged_path.read_text(encoding="utf-8"))
    original_path = Path(staged["original_path"])
    try:
        return ingest_document_bytes(
            original_path.read_bytes(),
            filename=str(staged["filename"]),
            content_type=staged.get("content_type"),
            document_id=document_id,
        )
    except Exception as exc:
        _atomic_write_text(
            _artifact_dir(document_id) / "status.json",
            json.dumps(
                {
                    "document_id": document_id,
                    "status": "failed",
                    "page_count": 0,
                    "ready_pages": 0,
                    "partial_pages": 0,
                    "unavailable_pages": 0,
                    "warnings": [{"warning": f"{type(exc).__name__}: {exc}"}],
                },
                indent=2,
            ),
        )
        raise


def _page_markdown(page_number: int, text: str) -> str:
    clean = text.replace("\r\n", "\n").strip()
    body = clean if clean else "_No extractable text on this page._"
    return f"<!-- source-page: {page_number} -->\n\n## Page {page_number}\n\n{body}"


def _blocks_for_page(page_number: int, text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block_index, match in enumerate(re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", text, re.DOTALL)):
        block_text = match.group(0).strip()
        if not block_text:
            continue
        blocks.append(
            {
                "block_id": f"p{page_number}-b{block_index + 1}",
                "page": page_number,
                "type": "text",
                "text": block_text,
                "start_char": match.start(),
                "end_char": match.end(),
            }
        )
    return blocks


def build_converted_manual(
    pages: list[dict[str, Any]],
    *,
    document_id: str,
    filename: str,
    converter: Callable[[dict[str, Any]], str] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Create deterministic page-marked Markdown and its source block manifest.

    ``converter`` is the adapter seam for a future VLM implementation. It is
    invoked once per page and must return Markdown derived from that page. If it
    fails, native extraction is retained and the page is marked partial.
    """
    markdown_pages: list[str] = []
    manifest_blocks: list[dict[str, Any]] = []
    page_statuses: list[dict[str, Any]] = []
    for position, page_record in enumerate(pages, start=1):
        source_page = page_record.get("page")
        if not isinstance(source_page, int):
            source_page = position
        native_text = str(page_record.get("text") or "").replace("\r\n", "\n").strip()
        converted_text = native_text
        conversion_mode = "native_text"
        warnings: list[str] = []
        if converter is not None:
            try:
                candidate = converter(dict(page_record))
                if isinstance(candidate, str) and candidate.strip():
                    converted_text = candidate.strip()
                    conversion_mode = "adapter"
                else:
                    warnings.append("converter_returned_empty_output")
            except Exception:
                warnings.append("converter_failed_native_text_used")
        if not converted_text:
            warnings.append("no_extractable_text")
        status = "ready" if not warnings else ("partial" if converted_text else "unavailable")
        markdown_pages.append(_page_markdown(source_page, converted_text))
        blocks = _blocks_for_page(source_page, converted_text)
        manifest_blocks.extend(blocks)
        page_statuses.append(
            {
                "page": source_page,
                "status": status,
                "conversion_mode": conversion_mode,
                "warnings": warnings,
                "block_ids": [block["block_id"] for block in blocks],
            }
        )

    markdown = "\n\n---\n\n".join(markdown_pages).rstrip() + "\n"
    manifest = {
        "version": 1,
        "document_id": document_id,
        "source_filename": filename,
        "page_count": len(pages),
        "blocks": manifest_blocks,
        "pages": page_statuses,
    }
    return markdown, manifest, page_statuses


def _write_conversion_artifact(
    document_id: str,
    filename: str,
    pages: list[dict[str, Any]],
    converter: Callable[[dict[str, Any]], str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    markdown, manifest, page_statuses = build_converted_manual(
        pages,
        document_id=document_id,
        filename=filename,
        converter=converter,
    )
    artifact_dir = _artifact_dir(document_id)
    _atomic_write_text(artifact_dir / "converted.md", markdown)
    _atomic_write_text(artifact_dir / "manifest.json", json.dumps(manifest, indent=2))
    overall_status = (
        "ready"
        if page_statuses and all(page["status"] == "ready" for page in page_statuses)
        else "partial"
    )
    status = {
        "document_id": document_id,
        "status": overall_status,
        "page_count": len(pages),
        "ready_pages": sum(page["status"] == "ready" for page in page_statuses),
        "partial_pages": sum(page["status"] == "partial" for page in page_statuses),
        "unavailable_pages": sum(page["status"] == "unavailable" for page in page_statuses),
        "warnings": [
            {"page": page["page"], "warning": warning}
            for page in page_statuses
            for warning in page["warnings"]
        ],
    }
    _atomic_write_text(artifact_dir / "status.json", json.dumps(status, indent=2))
    converted_pages = [
        {
            "page": page_status["page"],
            "text": "\n\n".join(
                block["text"]
                for block in manifest["blocks"]
                if block["page"] == page_status["page"]
            ),
            "source_kind": "converted_manual_page",
        }
        for page_status in page_statuses
    ]
    return status, converted_pages


def ingest_document_pages(
    pages: list[dict[str, Any]],
    *,
    filename: str,
    document_id: str | None = None,
    content_type: str | None = None,
    original_data: bytes | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
    converter: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    normalized_pages = [
        {
            **page,
            "text": str(page.get("text") or "").replace("\r\n", "\n").strip(),
        }
        for page in pages
    ]
    native_source_text = "\n\n".join(page["text"] for page in normalized_pages if page["text"])
    checksum_source = (
        original_data
        if original_data is not None
        else f"{filename}\0{native_source_text}".encode("utf-8")
    )
    checksum = hashlib.sha256(checksum_source).hexdigest()
    resolved_document_id = document_id or checksum[:16]
    _validated_document_id(resolved_document_id)
    document_version = checksum
    is_pdf = filename.lower().endswith(".pdf") or content_type == "application/pdf"
    conversion_status, converted_pages = _write_conversion_artifact(
        resolved_document_id,
        filename,
        normalized_pages,
        converter=converter,
    )
    if not is_pdf:
        for converted_page in converted_pages:
            converted_page["page"] = None
    converted_source_text = "\n\n".join(page["text"] for page in converted_pages if page["text"])
    if not converted_source_text:
        raise ValueError("Document contained no extractable or converted text")

    chunk_records: list[dict[str, Any]] = []
    for page_record in converted_pages:
        page_number = page_record.get("page")
        page_chunks = _chunk_text_with_offsets(page_record["text"], chunk_size, overlap)
        for page_chunk_index, page_chunk in enumerate(page_chunks):
            chunk_records.append(
                {
                    **page_chunk,
                    "page": page_number if isinstance(page_number, int) else None,
                    "page_chunk_index": page_chunk_index,
                }
            )
    if not chunk_records:
        raise ValueError("Document contained no extractable text")

    chunk_texts = [chunk["text"] for chunk in chunk_records]
    embeddings = (embedder or _embed_texts_openrouter)(chunk_texts)
    if len(embeddings) != len(chunk_records):
        raise ValueError("Embedding function must return one vector per chunk")

    created_at = time.time()
    document = {
        "id": resolved_document_id,
        "name": filename,
        "source": "user",
        "content_type": content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "created_at": created_at,
        "updated_at": created_at,
        "version": document_version,
        "checksum": checksum,
        "chunk_count": len(chunk_records),
        "page_count": len(normalized_pages) if is_pdf else None,
        "processing_status": conversion_status["status"],
        "citation_status": "ready" if is_pdf else "locator_unavailable",
    }
    rag_chunks: list[dict[str, Any]] = []
    for global_index, (chunk, embedding) in enumerate(zip(chunk_records, embeddings, strict=True)):
        page_number = chunk["page"]
        page_locator = f"p{page_number}" if page_number is not None else "unpaged"
        chunk_id = f"{resolved_document_id}:{page_locator}:c{chunk['page_chunk_index']}"
        rag_chunks.append(
            {
                "id": chunk_id,
                "citation_id": f"evidence:{chunk_id}",
                "document_id": resolved_document_id,
                "document_version": document_version,
                "filename": filename,
                "source": "user",
                "source_kind": "document",
                "index": global_index,
                "page_chunk_index": chunk["page_chunk_index"],
                "page": page_number,
                "text": chunk["text"],
                "start_char": chunk["start_char"],
                "end_char": chunk["end_char"],
                "citation_status": "ready" if page_number is not None else "locator_unavailable",
                "embedding": embedding,
                "embedding_model": EMBEDDING_MODEL
                if os.getenv("OPENROUTER_API_KEY", "").strip()
                else "local-hash-fallback",
            }
        )

    with INDEX_LOCK:
        index = _upsert_document(_load_index(), document, rag_chunks)
        _save_index(index)
    _atomic_write_text(_files_dir() / f"{resolved_document_id}.txt", converted_source_text)
    if original_data is not None:
        original_path = _originals_dir() / f"{resolved_document_id}{_safe_original_suffix(filename, content_type)}"
        original_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = original_path.with_suffix(original_path.suffix + ".tmp")
        temporary.write_bytes(original_data)
        temporary.replace(original_path)

    # Keep a per-document JSON snapshot for backwards compatibility and local inspection.
    _atomic_write_text(
        _legacy_document_path(resolved_document_id),
        json.dumps(
            {
                "document_id": resolved_document_id,
                "document_version": document_version,
                "filename": filename,
                "content_type": document["content_type"],
                "created_at": created_at,
                "chunk_count": len(rag_chunks),
                "chunks": [
                    {
                        "chunk_id": chunk["id"],
                        "citation_id": chunk["citation_id"],
                        "index": chunk["index"],
                        "page": chunk["page"],
                        "start_char": chunk["start_char"],
                        "end_char": chunk["end_char"],
                        "text": chunk["text"],
                        "embedding": chunk["embedding"],
                    }
                    for chunk in rag_chunks
                ],
            },
            indent=2,
        ),
    )

    return {
        "document_id": resolved_document_id,
        "document_version": document_version,
        "filename": filename,
        "chunk_count": len(rag_chunks),
        "page_count": document["page_count"],
        "status": conversion_status["status"],
        "citation_status": document["citation_status"],
    }


def ingest_document_bytes(
    data: bytes,
    *,
    filename: str,
    content_type: str | None = None,
    document_id: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
    converter: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    pages = extract_pages_from_bytes(filename, content_type, data)
    if (
        converter is None
        and any(page.get("image_data_url") for page in pages)
        and os.getenv("RAGVLM_VLM_CONVERSION_ENABLED", "true").lower() == "true"
    ):
        converter = _convert_page_with_vlm
    return ingest_document_pages(
        pages,
        filename=filename,
        document_id=document_id,
        content_type=content_type,
        original_data=data,
        chunk_size=chunk_size,
        overlap=overlap,
        embedder=embedder,
        converter=converter,
    )


def ingest_document_text(
    text: str,
    *,
    filename: str = "document.txt",
    document_id: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for existing callers and tests."""
    data = text.encode("utf-8")
    return ingest_document_pages(
        [{"page": None, "text": text, "source_kind": "document"}],
        filename=filename,
        document_id=document_id,
        content_type="text/plain",
        original_data=data,
        chunk_size=chunk_size,
        overlap=overlap,
        embedder=embedder,
    )


def get_document_record(document_id: str) -> dict[str, Any] | None:
    return next(
        (
            document
            for document in _load_index().get("documents", [])
            if isinstance(document, dict) and document.get("id") == document_id
        ),
        None,
    )


def get_document_status(document_id: str) -> dict[str, Any]:
    _validated_document_id(document_id)
    document = get_document_record(document_id)
    status_path = _artifact_dir(document_id) / "status.json"
    if document is None and not status_path.exists():
        raise KeyError(document_id)
    conversion = (
        json.loads(status_path.read_text(encoding="utf-8"))
        if status_path.exists()
        else {
            "document_id": document_id,
            "status": document.get("processing_status", "legacy") if document else "legacy",
        }
    )
    staged_path = _artifact_dir(document_id) / "staged.json"
    staged = (
        json.loads(staged_path.read_text(encoding="utf-8"))
        if staged_path.exists()
        else {}
    )
    return {
        **conversion,
        "filename": document.get("name", "document") if document else staged.get("filename", "document"),
        "document_version": document.get("version") if document else staged.get("document_version"),
        "chunk_count": document.get("chunk_count", 0) if document else 0,
        "citation_status": (
            document.get("citation_status", "page_unavailable")
            if document
            else staged.get("citation_status", "processing")
        ),
        "original_available": _find_original_path(document_id) is not None,
        "converted_text_available": (_artifact_dir(document_id) / "converted.md").exists(),
    }


def get_converted_manual(document_id: str) -> dict[str, Any]:
    document = get_document_record(document_id)
    markdown_path = _artifact_dir(document_id) / "converted.md"
    manifest_path = _artifact_dir(document_id) / "manifest.json"
    if document is None or not markdown_path.exists() or not manifest_path.exists():
        raise KeyError(document_id)
    return {
        "document_id": document_id,
        "document_version": document.get("version"),
        "source_filename": document.get("name", "document"),
        "status": document.get("processing_status", "ready"),
        "markdown": markdown_path.read_text(encoding="utf-8"),
        "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
    }


def converted_manual_path(document_id: str) -> Path:
    path = _artifact_dir(document_id) / "converted.md"
    if get_document_record(document_id) is None or not path.exists():
        raise KeyError(document_id)
    return path


def reprocess_document(
    document_id: str,
    *,
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
    converter: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    document = get_document_record(document_id)
    original_path = _find_original_path(document_id)
    if document is None:
        raise KeyError(document_id)
    if original_path is None:
        raise ValueError("Original document is unavailable; upload it again to reprocess")
    return ingest_document_bytes(
        original_path.read_bytes(),
        filename=str(document.get("name") or original_path.name),
        content_type=str(document.get("content_type") or mimetypes.guess_type(original_path.name)[0] or ""),
        document_id=document_id,
        embedder=embedder,
        converter=converter,
    )


def _candidate_chunks(index: dict[str, Any], document_ids: list[str] | None) -> list[dict[str, Any]]:
    chunks = [chunk for chunk in index.get("chunks", []) if isinstance(chunk, dict)]
    if not document_ids:
        return chunks
    id_set = set(document_ids)
    return [chunk for chunk in chunks if chunk.get("document_id") in id_set]


def _format_chunk(chunk: dict[str, Any], score: float) -> dict[str, Any]:
    return {
        "citation_id": chunk.get("citation_id") or f"evidence:{chunk.get('id')}",
        "source_kind": chunk.get("source_kind", "document"),
        "document_id": chunk.get("document_id"),
        "document_version": chunk.get("document_version"),
        "filename": chunk.get("filename", "document"),
        "chunk_id": chunk.get("id"),
        "index": chunk.get("index", 0),
        "page": chunk.get("page"),
        "page_chunk_index": chunk.get("page_chunk_index"),
        "block_id": chunk.get("block_id"),
        "section": chunk.get("section"),
        "start_char": chunk.get("start_char"),
        "end_char": chunk.get("end_char"),
        "citation_status": chunk.get(
            "citation_status",
            "ready" if chunk.get("page") is not None else "page_unavailable",
        ),
        "text": chunk.get("text", ""),
        "excerpt": chunk.get("text", ""),
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

    with INDEX_LOCK:
        index = _load_index()
    candidates = _candidate_chunks(index, document_ids)
    if not candidates:
        return []

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
