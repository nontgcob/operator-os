from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pypdf import PdfWriter


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _load_app(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MULTIMODAL_RAG_DATA_DIR", str(tmp_path / "multimodal"))
    monkeypatch.setenv("MULTIMODAL_RAG_ANSWERER", "offline")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.syspath_prepend(str(SERVICE_ROOT))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    module = importlib.import_module("app.main")
    return module, TestClient(module.app)


def _blank_pdf(page_count: int = 2) -> bytes:
    from io import BytesIO

    output = BytesIO()
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    writer.write(output)
    return output.getvalue()


def test_health_discloses_independent_pipeline(monkeypatch, tmp_path) -> None:
    _, client = _load_app(monkeypatch, tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["pipeline"] == "multimodal_rag"
    assert body["retriever"] == "deterministic-page-index-v1"
    assert body["answerer"] == "deterministic-offline-v1"


def test_ingest_persists_original_and_page_index(monkeypatch, tmp_path) -> None:
    _, client = _load_app(monkeypatch, tmp_path)
    pdf = _blank_pdf(2)

    response = client.post(
        "/documents/ingest",
        files={"file": ("manual.pdf", pdf, "application/pdf")},
        data={"document_id": "manual-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == "manual-1"
    assert body["page_count"] == 2
    document_dir = tmp_path / "multimodal" / "documents" / "manual-1"
    assert (document_dir / "original.pdf").read_bytes() == pdf
    assert (document_dir / "manifest.json").is_file()
    assert (document_dir / "page-index.json").is_file()

    status = client.get("/documents/manual-1/status")
    assert status.status_code == 200
    assert status.json()["page_count"] == 2


def test_ingest_rejects_non_pdf_and_unsafe_id(monkeypatch, tmp_path) -> None:
    _, client = _load_app(monkeypatch, tmp_path)

    non_pdf = client.post(
        "/documents/ingest",
        files={"file": ("notes.txt", b"not pdf", "application/octet-stream")},
    )
    unsafe_id = client.post(
        "/documents/ingest",
        files={"file": ("manual.pdf", _blank_pdf(), "application/pdf")},
        data={"document_id": "../escape"},
    )

    assert non_pdf.status_code == 400
    assert unsafe_id.status_code == 400
    assert not (tmp_path / "escape").exists()


def test_ask_returns_page_citations_and_no_internal_knowledge(
    monkeypatch, tmp_path
) -> None:
    module, client = _load_app(monkeypatch, tmp_path)
    ingest = client.post(
        "/documents/ingest",
        files={"file": ("lathe.pdf", _blank_pdf(), "application/pdf")},
        data={"document_id": "lathe"},
    )
    assert ingest.status_code == 200
    manifest = module.store.get("lathe")
    manifest.pages[0].text = "Turn the red isolation switch before opening the guard."
    manifest_path = (
        tmp_path / "multimodal" / "documents" / "lathe" / "manifest.json"
    )
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    answer = client.post(
        "/rag/multimodal/answer",
        json={
            "question": "Which isolation switch should I turn?",
            "document_ids": ["lathe"],
            "conversation": [{"role": "user", "content": "I am at the lathe."}],
            "model": "test-model",
        },
    )

    assert answer.status_code == 200
    body = answer.json()
    assert body["provenance"] == "document"
    assert body["model_knowledge"] == {"used": False, "disclosure": None}
    assert body["citations"][0]["filename"] == "lathe.pdf"
    assert body["citations"][0]["page"] == 1
    assert body["citations"][0]["citation_id"] == "C1"
    assert "[C1]" in body["text"]
    assert body["annotations"] == []
    assert body["tracking_prompt"] is None
    assert body["tracking_annotations"] == []
    assert body["error"] is None


def test_ask_missing_document_and_no_relevant_evidence(monkeypatch, tmp_path) -> None:
    _, client = _load_app(monkeypatch, tmp_path)

    missing = client.post(
        "/rag/multimodal/answer",
        json={"question": "How?", "document_ids": ["missing"]},
    )
    assert missing.status_code == 404

    client.post(
        "/documents/ingest",
        files={"file": ("blank.pdf", _blank_pdf(), "application/pdf")},
        data={"document_id": "blank"},
    )
    answer = client.post(
        "/rag/multimodal/answer",
        json={"question": "How do I calibrate it?", "document_ids": ["blank"]},
    )
    assert answer.status_code == 200
    assert answer.json()["provenance"] == "insufficient"
    assert answer.json()["citations"] == []


def test_server_rejects_invented_citations_and_discloses_model_knowledge(
    monkeypatch, tmp_path
) -> None:
    module, client = _load_app(monkeypatch, tmp_path)
    client.post(
        "/documents/ingest",
        files={"file": ("blank.pdf", _blank_pdf(), "application/pdf")},
        data={"document_id": "blank"},
    )

    class FakeAnswerer:
        name = "test-answerer"

        async def answer(
            self,
            question,
            evidence,
            allow_model_knowledge,
            conversation=None,
            model=None,
        ):
            del question, evidence, allow_model_knowledge, conversation, model
            return module.AnswerResult(
                text="General guidance [C99].",
                model_knowledge_used=True,
            )

    module.answerer = FakeAnswerer()
    response = client.post(
        "/rag/multimodal/answer",
        json={
            "question": "What is the general guidance?",
            "document_ids": ["blank"],
            "allow_model_knowledge": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == []
    assert body["provenance"] == "model_knowledge"
    assert body["model_knowledge"]["used"] is True
    assert "internal knowledge" in body["model_knowledge"]["disclosure"]
    assert "[C99]" not in body["text"]


def test_document_only_answer_falls_back_to_retrieved_evidence_when_uncited(
    monkeypatch, tmp_path
) -> None:
    module, client = _load_app(monkeypatch, tmp_path)

    class FakeRetriever:
        name = "test-retriever"

        def retrieve(self, question, document_ids, top_k):
            del question, document_ids, top_k
            return [
                SimpleNamespace(
                    manifest=SimpleNamespace(
                        document_id="lathe",
                        version="version-1",
                        filename="lathe.pdf",
                    ),
                    page=SimpleNamespace(
                        page=1,
                        text="Set the feed switch to AUTO.",
                        image_path=None,
                    ),
                    score=1.0,
                )
            ]

    class FakeAnswerer:
        name = "test-answerer"

        async def answer(
            self,
            question,
            evidence,
            allow_model_knowledge,
            conversation=None,
            model=None,
        ):
            del question, evidence, allow_model_knowledge, conversation, model
            return module.AnswerResult(
                text="The supplied evidence is insufficient.",
                model_knowledge_used=False,
            )

    module.retriever = FakeRetriever()
    module.answerer = FakeAnswerer()
    response = client.post(
        "/rag/multimodal/answer",
        json={
            "question": "What should the feed switch be set to?",
            "document_ids": ["lathe"],
            "allow_model_knowledge": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provenance"] == "document"
    assert body["status"] == "complete"
    assert "Set the feed switch to AUTO" in body["text"]
    assert body["citations"][0]["citation_id"] == "C1"
