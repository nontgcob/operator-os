from __future__ import annotations

import base64
import sys
from pathlib import Path

RAGVLM_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "ragvlm-service" / "app"
sys.path.append(str(RAGVLM_APP_PATH))

from rag.retrieval import (  # type: ignore  # noqa: E402
    chunk_text,
    configure_document_dir,
    get_document_file_content_parts,
    get_document_status,
    ingest_document_bytes,
    retrieve_chunks,
)


def test_chunk_text_compatibility_helper_still_overlaps_adjacent_chunks() -> None:
    chunks = chunk_text("abcdefghijklmnopqrstuvwxyz", chunk_size=10, overlap=2)

    assert len(chunks) == 3
    assert chunks[0][-2:] == chunks[1][:2]
    assert chunks[1][-2:] == chunks[2][:2]


def test_pdf_ingest_stores_original_for_direct_vlm_use(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)
    data = b"%PDF-1.4\nfixture"

    result = ingest_document_bytes(
        data,
        filename="manual.pdf",
        content_type="application/pdf",
        document_id="manual",
    )
    status = get_document_status("manual")
    parts = get_document_file_content_parts(["manual"])

    assert result["status"] == "queryable"
    assert result["chunk_count"] == 0
    assert result["interaction_mode"] == "direct_pdf_vlm"
    assert status["converted_text_available"] is False
    assert status["original_available"] is True
    assert retrieve_chunks("anything", ["manual"]) == []
    assert parts == [
        {
            "type": "file",
            "file": {
                "filename": "manual.pdf",
                "file_data": f"data:application/pdf;base64,{base64.b64encode(data).decode('ascii')}",
            },
        }
    ]
