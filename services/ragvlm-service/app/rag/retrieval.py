from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from threading import RLock
from typing import Any

try:
    from services.common.env import load_env_file
except ImportError:
    load_env_file = None

if load_env_file:
    load_env_file()

DOCUMENT_DIR = Path(os.getenv("RAGVLM_DOCUMENT_DIR", "data/ragvlm/documents"))
INDEX_FILENAME = "index.json"
ORIGINALS_DIRNAME = "originals"
ARTIFACTS_DIRNAME = "artifacts"
DOCUMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
INDEX_LOCK = RLock()


def configure_document_dir(path: Path) -> None:
    global DOCUMENT_DIR
    DOCUMENT_DIR = path


def _index_path() -> Path:
    return DOCUMENT_DIR / INDEX_FILENAME


def _originals_dir() -> Path:
    return DOCUMENT_DIR / ORIGINALS_DIRNAME


def _artifacts_dir() -> Path:
    return DOCUMENT_DIR / ARTIFACTS_DIRNAME


def _artifact_dir(document_id: str) -> Path:
    return _artifacts_dir() / document_id


def _ensure_document_dir() -> None:
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    _originals_dir().mkdir(parents=True, exist_ok=True)
    _artifacts_dir().mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _empty_index() -> dict[str, Any]:
    return {"version": 3, "documents": []}


def _load_index() -> dict[str, Any]:
    _ensure_document_dir()
    path = _index_path()
    if not path.exists():
        return _empty_index()
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty_index()
    if not isinstance(index, dict):
        return _empty_index()
    documents = index.get("documents", [])
    return {"version": 3, "documents": documents if isinstance(documents, list) else []}


def _save_index(index: dict[str, Any]) -> None:
    _ensure_document_dir()
    temporary = _index_path().with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, indent=2), encoding="utf-8")
    temporary.replace(_index_path())


def _validated_document_id(document_id: str) -> str:
    if not DOCUMENT_ID_RE.fullmatch(document_id):
        raise ValueError(
            "document_id must start with an alphanumeric character and contain only "
            "letters, numbers, '.', '_' or '-'."
        )
    return document_id


def _safe_original_suffix(filename: str, content_type: str | None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix and re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        return suffix
    guessed = mimetypes.guess_extension(content_type or "")
    return guessed if guessed and re.fullmatch(r"\.[a-z0-9]{1,10}", guessed) else ".bin"


def _find_original_path(document_id: str) -> Path | None:
    matches = sorted(_originals_dir().glob(f"{document_id}.*"))
    return matches[0] if matches else None


def _is_pdf(filename: str, content_type: str | None) -> bool:
    return filename.lower().endswith(".pdf") or content_type == "application/pdf"


def _record_for_upload(
    *,
    document_id: str,
    checksum: str,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    original_path: Path,
) -> dict[str, Any]:
    now = time.time()
    return {
        "id": document_id,
        "name": filename,
        "source": "user",
        "content_type": content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "created_at": now,
        "updated_at": now,
        "version": checksum,
        "checksum": checksum,
        "size_bytes": size_bytes,
        "original_path": str(original_path),
        "processing_status": "queryable",
        "interaction_mode": "direct_pdf_vlm" if _is_pdf(filename, content_type) else "direct_file_vlm",
        "chunk_count": 0,
        "page_count": None,
        "citation_status": "model_native",
    }


def _upsert_document(document: dict[str, Any]) -> None:
    document_id = document["id"]
    with INDEX_LOCK:
        index = _load_index()
        index["documents"] = [
            item for item in index.get("documents", []) if not isinstance(item, dict) or item.get("id") != document_id
        ] + [document]
        _save_index(index)


def _write_status(document: dict[str, Any]) -> None:
    _atomic_write_text(
        _artifact_dir(str(document["id"])) / "status.json",
        json.dumps(
            {
                "document_id": document["id"],
                "status": document["processing_status"],
                "filename": document["name"],
                "document_version": document["version"],
                "interaction_mode": document["interaction_mode"],
                "chunk_count": 0,
                "citation_status": document["citation_status"],
                "original_available": True,
                "converted_text_available": False,
                "warnings": [],
            },
            indent=2,
        ),
    )


def ingest_document_bytes(
    data: bytes,
    *,
    filename: str,
    content_type: str | None = None,
    document_id: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    _ensure_document_dir()
    checksum = hashlib.sha256(data).hexdigest()
    resolved_document_id = _validated_document_id(document_id or checksum[:16])
    original_path = _originals_dir() / f"{resolved_document_id}{_safe_original_suffix(filename, content_type)}"
    temporary = original_path.with_suffix(original_path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(original_path)
    document = _record_for_upload(
        document_id=resolved_document_id,
        checksum=checksum,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        original_path=original_path,
    )
    _upsert_document(document)
    _write_status(document)
    return {
        "document_id": resolved_document_id,
        "document_version": checksum,
        "filename": filename,
        "chunk_count": 0,
        "status": document["processing_status"],
        "citation_status": document["citation_status"],
        "interaction_mode": document["interaction_mode"],
    }


def stage_document_bytes(
    data: bytes,
    *,
    filename: str,
    content_type: str | None,
    document_id: str,
) -> dict[str, Any]:
    return ingest_document_bytes(
        data,
        filename=filename,
        content_type=content_type,
        document_id=document_id,
    )


def process_staged_document(document_id: str) -> dict[str, Any]:
    document = get_document_record(document_id)
    if document is None:
        raise KeyError(document_id)
    return {
        "document_id": document_id,
        "document_version": document.get("version"),
        "filename": document.get("name", "document"),
        "chunk_count": 0,
        "status": document.get("processing_status", "queryable"),
        "citation_status": document.get("citation_status", "model_native"),
        "interaction_mode": document.get("interaction_mode", "direct_file_vlm"),
    }


def get_document_record(document_id: str) -> dict[str, Any] | None:
    _validated_document_id(document_id)
    return next(
        (
            document
            for document in _load_index().get("documents", [])
            if isinstance(document, dict) and document.get("id") == document_id
        ),
        None,
    )


def get_document_status(document_id: str) -> dict[str, Any]:
    document = get_document_record(document_id)
    if document is None:
        status_path = _artifact_dir(_validated_document_id(document_id)) / "status.json"
        if not status_path.exists():
            raise KeyError(document_id)
        return json.loads(status_path.read_text(encoding="utf-8"))
    return {
        "document_id": document_id,
        "status": document.get("processing_status", "queryable"),
        "filename": document.get("name", "document"),
        "document_version": document.get("version"),
        "interaction_mode": document.get("interaction_mode", "direct_file_vlm"),
        "chunk_count": 0,
        "citation_status": document.get("citation_status", "model_native"),
        "original_available": _find_original_path(document_id) is not None,
        "converted_text_available": False,
        "warnings": [],
    }


def reprocess_document(document_id: str, **_: Any) -> dict[str, Any]:
    return process_staged_document(document_id)


def get_document_file_content_parts(document_ids: list[str]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for document_id in document_ids:
        document = get_document_record(document_id)
        original_path = _find_original_path(document_id)
        if document is None or original_path is None:
            raise KeyError(document_id)
        content_type = str(document.get("content_type") or mimetypes.guess_type(original_path.name)[0] or "")
        if content_type != "application/pdf" and not original_path.name.lower().endswith(".pdf"):
            raise ValueError(f"{document_id} is not a PDF document")
        encoded = base64.b64encode(original_path.read_bytes()).decode("ascii")
        parts.append(
            {
                "type": "file",
                "file": {
                    "filename": str(document.get("name") or original_path.name),
                    "file_data": f"data:application/pdf;base64,{encoded}",
                },
            }
        )
    return parts


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> list[str]:
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
        chunks.append(normalized[start:end])
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


def retrieve_chunks(*_: Any, **__: Any) -> list[dict[str, Any]]:
    return []


def ingest_document_text(
    text: str,
    *,
    filename: str = "document.txt",
    document_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return ingest_document_bytes(
        text.encode("utf-8"),
        filename=filename,
        content_type="text/plain",
        document_id=document_id,
        **kwargs,
    )


def get_converted_manual(document_id: str) -> dict[str, Any]:
    raise KeyError(document_id)


def converted_manual_path(document_id: str) -> Path:
    raise KeyError(document_id)
