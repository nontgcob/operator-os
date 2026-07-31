from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

RAGVLM_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "ragvlm-service" / "app"
sys.path.insert(0, str(RAGVLM_APP_PATH))

import main as ragvlm_main  # type: ignore  # noqa: E402
from rag.retrieval import (  # type: ignore  # noqa: E402
    configure_document_dir,
    get_document_status,
    ingest_document_bytes,
    reprocess_document,
)


def test_document_status_reports_direct_pdf_mode(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)
    ingest_document_bytes(
        b"%PDF-1.4\nfixture",
        filename="manual.pdf",
        content_type="application/pdf",
        document_id="manual",
    )

    status = get_document_status("manual")

    assert status["status"] == "queryable"
    assert status["interaction_mode"] == "direct_pdf_vlm"
    assert status["chunk_count"] == 0
    assert status["citation_status"] == "model_native"
    assert status["converted_text_available"] is False


def test_reprocess_keeps_stored_document_queryable(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)
    first = ingest_document_bytes(
        b"%PDF-1.4\nfixture",
        filename="manual.pdf",
        content_type="application/pdf",
        document_id="manual",
    )
    second = reprocess_document("manual")

    assert second["document_id"] == first["document_id"]
    assert second["document_version"] == first["document_version"]
    assert second["status"] == "queryable"


def test_removed_text_artifact_and_text_rag_endpoints_return_410(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)
    client = TestClient(ragvlm_main.app)

    converted = client.get("/documents/manual/converted-text")
    retrieve = client.post("/documents/retrieve", json={"question": "x", "document_ids": ["manual"]})
    text_answer = client.post("/rag/text/answer", json={"question": "x", "document_ids": ["manual"]})

    assert converted.status_code == 410
    assert retrieve.status_code == 410
    assert text_answer.status_code == 410
