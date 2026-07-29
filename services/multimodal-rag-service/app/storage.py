from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .models import DocumentManifest, PageRecord


DOCUMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PIPELINE_VERSION = os.getenv("MULTIMODAL_RAG_PIPELINE_VERSION", "0.1.0")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class OriginalPdfStore:
    """Owns original PDFs, rendered pages, and the standalone page index."""

    def __init__(self, root: Path, render_dpi: int = 200) -> None:
        self.root = root.resolve()
        self.documents_root = self.root / "documents"
        self.documents_root.mkdir(parents=True, exist_ok=True)
        self.render_dpi = render_dpi

    def ingest(
        self,
        data: bytes,
        filename: str,
        requested_document_id: str | None = None,
    ) -> DocumentManifest:
        if not data.startswith(b"%PDF-"):
            raise ValueError("Only PDF uploads are supported by multimodal RAG.")
        checksum = hashlib.sha256(data).hexdigest()
        document_id = requested_document_id or checksum[:24]
        if not DOCUMENT_ID_PATTERN.fullmatch(document_id):
            raise ValueError(
                "document_id must start with an alphanumeric character and contain only "
                "letters, numbers, '.', '_' or '-'."
            )

        document_dir = self.documents_root / document_id
        document_dir.mkdir(parents=True, exist_ok=True)
        original_path = document_dir / "original.pdf"
        temporary_original = original_path.with_suffix(".pdf.tmp")
        temporary_original.write_bytes(data)
        temporary_original.replace(original_path)

        pages, warnings = self._extract_and_render(data, document_dir)
        if not pages:
            raise ValueError("The PDF contains no readable pages.")
        partial = any(page.render_status == "failed" for page in pages)
        status = "partial" if partial else "ready"
        manifest = DocumentManifest(
            document_id=document_id,
            version=f"{PIPELINE_VERSION}:{checksum[:12]}",
            filename=Path(filename or "manual.pdf").name,
            checksum=checksum,
            status=status,
            page_count=len(pages),
            pages=pages,
            warnings=warnings,
            index_adapter="deterministic-page-index-v1",
            created_at=_utc_now(),
        )
        _atomic_json(document_dir / "manifest.json", manifest.model_dump())
        _atomic_json(
            document_dir / "page-index.json",
            {
                "adapter": manifest.index_adapter,
                "document_id": document_id,
                "document_version": manifest.version,
                "pages": [
                    {
                        "page": page.page,
                        "text": page.text,
                        "image_path": page.image_path,
                    }
                    for page in pages
                ],
            },
        )
        return manifest

    def get(self, document_id: str) -> DocumentManifest:
        if not DOCUMENT_ID_PATTERN.fullmatch(document_id):
            raise KeyError(document_id)
        path = self.documents_root / document_id / "manifest.json"
        if not path.is_file():
            raise KeyError(document_id)
        return DocumentManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def reprocess(self, document_id: str) -> DocumentManifest:
        manifest = self.get(document_id)
        original_path = self.documents_root / document_id / "original.pdf"
        if not original_path.is_file():
            raise ValueError("The immutable original PDF is unavailable.")
        return self.ingest(
            original_path.read_bytes(),
            filename=manifest.filename,
            requested_document_id=document_id,
        )

    def image_file(self, document_id: str, image_path: str | None) -> Path | None:
        if not image_path:
            return None
        candidate = (self.documents_root / document_id / image_path).resolve()
        document_dir = (self.documents_root / document_id).resolve()
        if document_dir not in candidate.parents or not candidate.is_file():
            return None
        return candidate

    def _extract_and_render(
        self,
        data: bytes,
        document_dir: Path,
    ) -> tuple[list[PageRecord], list[str]]:
        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError:
            return self._extract_text_only(data)

        pages_dir = document_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        pages: list[PageRecord] = []
        warnings: list[str] = []
        try:
            pdf = fitz.open(stream=data, filetype="pdf")
            for page_index in range(pdf.page_count):
                page = pdf.load_page(page_index)
                page_warnings: list[str] = []
                image_relative: str | None = None
                status = "rendered"
                try:
                    scale = self.render_dpi / 72.0
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    image_relative = f"pages/page-{page_index + 1:04d}.png"
                    pixmap.save(str(document_dir / image_relative))
                except Exception as exc:  # page text remains useful if rendering fails
                    status = "text_only"
                    page_warnings.append(f"Page rendering failed: {type(exc).__name__}")
                pages.append(
                    PageRecord(
                        page=page_index + 1,
                        text=(page.get_text("text") or "").strip(),
                        image_path=image_relative,
                        render_status=status,
                        warnings=page_warnings,
                    )
                )
            pdf.close()
            return pages, warnings
        except Exception as exc:
            warnings.append(
                f"PyMuPDF processing failed ({type(exc).__name__}); used text-only fallback."
            )
            fallback_pages, fallback_warnings = self._extract_text_only(data)
            return fallback_pages, warnings + fallback_warnings

    @staticmethod
    def _extract_text_only(data: bytes) -> tuple[list[PageRecord], list[str]]:
        from io import BytesIO

        try:
            reader = PdfReader(BytesIO(data))
        except Exception as exc:
            raise ValueError(f"Unable to read PDF: {exc}") from exc
        pages: list[PageRecord] = []
        for page_index, page in enumerate(reader.pages):
            page_warnings = ["Page image unavailable because PyMuPDF is not installed."]
            try:
                text = (page.extract_text() or "").strip()
                status = "text_only"
            except Exception as exc:
                text = ""
                status = "failed"
                page_warnings.append(f"Text extraction failed: {type(exc).__name__}")
            pages.append(
                PageRecord(
                    page=page_index + 1,
                    text=text,
                    image_path=None,
                    render_status=status,
                    warnings=page_warnings,
                )
            )
        return pages, [
            "Page rendering is unavailable; install PyMuPDF to enable visual page retrieval."
        ]
