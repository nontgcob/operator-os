from __future__ import annotations

import asyncio
import json
import sys
from io import BytesIO
from pathlib import Path

RAGVLM_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "ragvlm-service" / "app"
sys.path.insert(0, str(RAGVLM_APP_PATH))

import main as ragvlm_main  # type: ignore  # noqa: E402
from rag.retrieval import (  # type: ignore  # noqa: E402
    build_converted_manual,
    configure_document_dir,
    extract_pages_from_bytes,
    get_converted_manual,
    get_document_status,
    ingest_document_bytes,
    ingest_document_pages,
    reprocess_document,
    retrieve_chunks,
    process_staged_document,
    stage_document_bytes,
)


def test_pdf_extraction_preserves_one_based_page_records() -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    data = BytesIO()
    writer.write(data)

    pages = extract_pages_from_bytes("manual.pdf", "application/pdf", data.getvalue())

    assert [page["page"] for page in pages] == [1, 2]
    assert all(page["source_kind"] == "pdf_page" for page in pages)


def test_page_chunks_never_cross_pages_and_return_citation_metadata(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)
    ingest_document_pages(
        [
            {"page": 1, "text": "FIRST_PAGE " * 12},
            {"page": 2, "text": "SECOND_PAGE " * 12},
        ],
        filename="pump.pdf",
        document_id="pump",
        content_type="application/pdf",
        original_data=b"%PDF-test-fixture",
        chunk_size=55,
        overlap=5,
    )

    chunks = retrieve_chunks("page contents", ["pump"], top_k=12)

    assert {chunk["page"] for chunk in chunks} == {1, 2}
    assert all(not ("FIRST_PAGE" in chunk["text"] and "SECOND_PAGE" in chunk["text"]) for chunk in chunks)
    assert all(chunk["citation_id"].startswith("evidence:pump:p") for chunk in chunks)
    assert all(chunk["document_version"] for chunk in chunks)
    assert all(chunk["start_char"] is not None and chunk["end_char"] is not None for chunk in chunks)


def test_conversion_artifact_has_page_markers_manifest_and_native_fallback(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)

    def converter(page: dict[str, object]) -> str:
        if page["page"] == 2:
            raise RuntimeError("offline adapter failure")
        return "# Converted heading\n\nConverted instructions."

    result = ingest_document_pages(
        [
            {"page": 1, "text": "Native first page."},
            {"page": 2, "text": "Native fallback instructions."},
        ],
        filename="manual.pdf",
        document_id="manual",
        content_type="application/pdf",
        original_data=b"original-pdf-bytes",
        converter=converter,
    )
    artifact = get_converted_manual("manual")
    status = get_document_status("manual")

    assert result["status"] == "partial"
    assert "<!-- source-page: 1 -->" in artifact["markdown"]
    assert "<!-- source-page: 2 -->" in artifact["markdown"]
    assert "Converted instructions." in artifact["markdown"]
    assert "Native fallback instructions." in artifact["markdown"]
    assert artifact["manifest"]["blocks"]
    assert artifact["manifest"]["pages"][1]["status"] == "partial"
    assert status["original_available"] is True
    assert status["converted_text_available"] is True
    assert {"page": 2, "warning": "converter_failed_native_text_used"} in status["warnings"]
    indexed = retrieve_chunks("converted", ["manual"], top_k=2)
    assert "Converted instructions." in indexed[0]["text"]
    assert indexed[0]["page"] == 1


def test_reprocess_uses_retained_original_for_text_compatibility(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)
    first = ingest_document_bytes(
        b"Valve maintenance instructions.",
        filename="manual.txt",
        content_type="text/plain",
        document_id="text-manual",
    )
    second = reprocess_document("text-manual")

    assert second["document_id"] == first["document_id"]
    assert second["document_version"] == first["document_version"]
    assert second["citation_status"] == "locator_unavailable"
    assert retrieve_chunks("valve", ["text-manual"], top_k=1)[0]["page"] is None


def test_text_answer_validates_citations_and_reports_mixed_provenance(monkeypatch) -> None:
    evidence = ragvlm_main.Evidence(
        citation_id="evidence:manual:p2:c0",
        document_id="manual",
        document_version="version-1",
        filename="manual.pdf",
        page=2,
        chunk_id="manual:p2:c0",
        excerpt="Close the lower valve before calibration.",
    )

    async def fake_completion(messages: list[dict[str, object]], model: str) -> str:
        assert "evidence:manual:p2:c0" in str(messages)
        return json.dumps(
            {
                "answer": "Close the lower valve [evidence:manual:p2:c0].",
                "citation_ids": ["evidence:manual:p2:c0", "invented:evidence"],
                "used_model_knowledge": True,
                "insufficient": False,
            }
        )

    monkeypatch.setattr(ragvlm_main, "_request_text_completion", fake_completion)
    monkeypatch.setattr(ragvlm_main, "OPENROUTER_API_KEY", "test-key")
    response = asyncio.run(
        ragvlm_main.text_rag_answer(
            ragvlm_main.TextAnswerRequest(
                question="What should I close?",
                evidence=[evidence],
            )
        )
    )

    assert response["status"] == "complete"
    assert response["provenance"] == "mixed"
    assert [citation["citation_id"] for citation in response["citations"]] == [
        "evidence:manual:p2:c0"
    ]
    assert response["citations"][0]["page"] == 2
    assert response["citations"][0]["filename"] == "manual.pdf"


def test_text_answer_recovers_inline_evidence_id_when_json_citation_ids_are_missing(
    monkeypatch,
) -> None:
    evidence = ragvlm_main.Evidence(
        citation_id="evidence:manual:p3:c0",
        document_id="manual",
        document_version="version-1",
        filename="manual.pdf",
        page=3,
        chunk_id="manual:p3:c0",
        excerpt="The filter is behind the rear service cover.",
    )

    async def fake_completion(messages: list[dict[str, object]], model: str) -> str:
        return json.dumps(
            {
                "answer": "The filter is behind the rear service cover [evidence:manual:p3:c0].",
                "citation_ids": [],
                "used_model_knowledge": False,
                "insufficient": False,
            }
        )

    monkeypatch.setattr(ragvlm_main, "_request_text_completion", fake_completion)
    monkeypatch.setattr(ragvlm_main, "OPENROUTER_API_KEY", "test-key")

    response = asyncio.run(
        ragvlm_main.text_rag_answer(
            ragvlm_main.TextAnswerRequest(
                question="Where is the filter?",
                evidence=[evidence],
                allow_model_knowledge=False,
            )
        )
    )

    assert response["provenance"] == "document"
    assert response["used_model_knowledge"] is False
    assert [citation["citation_id"] for citation in response["citations"]] == [
        "evidence:manual:p3:c0"
    ]


def test_text_answer_uses_evidence_fallback_when_model_claims_insufficient(
    monkeypatch,
) -> None:
    evidence = ragvlm_main.Evidence(
        citation_id="evidence:manual:p5:c0",
        document_id="manual",
        document_version="version-1",
        filename="manual.pdf",
        page=5,
        excerpt="Turn the selector to AUTO before starting the cycle.",
    )

    async def fake_completion(messages: list[dict[str, object]], model: str) -> str:
        return json.dumps(
            {
                "answer": "The supplied evidence is insufficient.",
                "citation_ids": [],
                "used_model_knowledge": False,
                "insufficient": True,
            }
        )

    monkeypatch.setattr(ragvlm_main, "_request_text_completion", fake_completion)
    monkeypatch.setattr(ragvlm_main, "OPENROUTER_API_KEY", "test-key")

    response = asyncio.run(
        ragvlm_main.text_rag_answer(
            ragvlm_main.TextAnswerRequest(
                question="What should I do before starting?",
                evidence=[evidence],
                allow_model_knowledge=False,
            )
        )
    )

    assert response["provenance"] == "document"
    assert response["used_model_knowledge"] is False
    assert "Turn the selector to AUTO" in response["text"]
    assert response["citations"][0]["citation_id"] == "evidence:manual:p5:c0"


def test_text_answer_has_deterministic_offline_evidence_fallback(monkeypatch) -> None:
    monkeypatch.setattr(ragvlm_main, "OPENROUTER_API_KEY", "")
    evidence = ragvlm_main.Evidence(
        citation_id="evidence:manual:p4:c0",
        document_id="manual",
        filename="manual.pdf",
        page=4,
        excerpt="Press RESET after the warning light turns off.",
    )

    response = asyncio.run(
        ragvlm_main.text_rag_answer(
            ragvlm_main.TextAnswerRequest(
                question="What do I press?",
                evidence=[evidence],
            )
        )
    )

    assert response["provenance"] == "document"
    assert "Press RESET" in response["text"]
    assert response["citations"][0]["citation_id"] == "evidence:manual:p4:c0"
    assert response["citations"][0]["page"] == 4


def test_text_answer_offline_without_evidence_is_insufficient(monkeypatch) -> None:
    monkeypatch.setattr(ragvlm_main, "OPENROUTER_API_KEY", "")

    response = asyncio.run(
        ragvlm_main.text_rag_answer(
            ragvlm_main.TextAnswerRequest(question="What do I press?")
        )
    )

    assert response["provenance"] == "insufficient"
    assert response["citations"] == []
    assert response["used_model_knowledge"] is False


def test_build_converted_manual_marks_empty_page_unavailable() -> None:
    markdown, manifest, statuses = build_converted_manual(
        [{"page": 1, "text": ""}],
        document_id="empty",
        filename="empty.pdf",
    )

    assert "_No extractable text on this page._" in markdown
    assert manifest["page_count"] == 1
    assert statuses[0]["status"] == "unavailable"


def test_staged_ingest_reports_processing_then_becomes_queryable(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)
    staged = stage_document_bytes(
        b"Lock the isolation switch before servicing.",
        filename="service.txt",
        content_type="text/plain",
        document_id="staged-manual",
    )

    assert staged["status"] == "processing"
    assert get_document_status("staged-manual")["status"] == "processing"

    completed = process_staged_document("staged-manual")

    assert completed["status"] == "ready"
    assert get_document_status("staged-manual")["chunk_count"] == 1
