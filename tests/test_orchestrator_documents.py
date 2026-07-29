from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def _load_orchestrator_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "orchestrator" / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("orchestrator_main_documents", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None, status_code: int = 200) -> None:
        self._payload = payload or {
            "chunks": [
                {"text": "manual chunk one"},
                {"text": ""},
                {"not_text": "ignored"},
                {"text": "manual chunk two"},
            ]
        }
        self.status_code = status_code
        self.text = "" if status_code < 400 else str(self._payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        self.requests.append({"url": url, "json": json})
        return _FakeResponse()


def test_retrieve_document_chunks_calls_ragvlm_service() -> None:
    module = _load_orchestrator_module()
    client = _FakeClient()

    chunks = asyncio.run(
        module._retrieve_document_chunks(
            client,
            ["doc-1"],
            "How do I calibrate this?",
            top_k=3,
        )
    )

    assert chunks == ["manual chunk one", "manual chunk two"]
    assert client.requests == [
        {
            "url": f"{module.RAGVLM_SERVICE_URL}/documents/retrieve",
            "json": {
                "question": "How do I calibrate this?",
                "document_ids": ["doc-1"],
                "top_k": 3,
            },
        }
    ]


def test_retrieve_document_chunks_skips_empty_document_ids() -> None:
    module = _load_orchestrator_module()
    client = _FakeClient()

    chunks = asyncio.run(module._retrieve_document_chunks(client, [], "question"))

    assert chunks == []
    assert client.requests == []


def test_document_status_treats_usable_partial_pipelines_as_queryable(monkeypatch) -> None:
    module = _load_orchestrator_module()

    async def fake_pipeline_status(url: str) -> dict[str, Any]:
        if "8001" in url:
            return {
                "status": "partial",
                "chunk_count": 12,
                "converted_text_available": True,
                "warnings": [{"page": 2, "warning": "converter_failed_native_text_used"}],
            }
        return {
            "status": "partial",
            "version": "multimodal-rag-v1:abc123",
            "page_count": 4,
            "rendered_pages": 3,
            "warnings": ["One page could not be rendered."],
        }

    monkeypatch.setattr(module, "_get_pipeline_status", fake_pipeline_status)

    response = asyncio.run(module.document_status("manual-1"))

    assert response["status"] == "queryable"
    assert response["pipelines"]["text_rag"]["status"] == "queryable"
    assert response["pipelines"]["text_rag"]["chunk_count"] == 12
    assert response["pipelines"]["multimodal_rag"]["status"] == "queryable"
    assert response["pipelines"]["multimodal_rag"]["page_count"] == 4


def test_document_status_keeps_partial_when_text_pipeline_has_no_chunks(monkeypatch) -> None:
    module = _load_orchestrator_module()

    async def fake_pipeline_status(url: str) -> dict[str, Any]:
        if "8001" in url:
            return {
                "status": "partial",
                "chunk_count": 0,
                "converted_text_available": False,
            }
        return {
            "status": "ready",
            "version": "multimodal-rag-v1:abc123",
            "page_count": 2,
            "rendered_pages": 2,
        }

    monkeypatch.setattr(module, "_get_pipeline_status", fake_pipeline_status)

    response = asyncio.run(module.document_status("manual-1"))

    assert response["status"] == "partial"
    assert response["pipelines"]["text_rag"]["status"] == "partial"
    assert response["pipelines"]["multimodal_rag"]["status"] == "queryable"


def test_document_status_keeps_processing_until_both_pipelines_are_done(monkeypatch) -> None:
    module = _load_orchestrator_module()

    async def fake_pipeline_status(url: str) -> dict[str, Any]:
        if "8001" in url:
            return {"status": "processing", "chunk_count": 0}
        return {
            "status": "ready",
            "version": "multimodal-rag-v1:abc123",
            "page_count": 2,
            "rendered_pages": 2,
        }

    monkeypatch.setattr(module, "_get_pipeline_status", fake_pipeline_status)

    response = asyncio.run(module.document_status("manual-1"))

    assert response["status"] == "processing"
    assert response["pipelines"]["text_rag"]["status"] == "processing"
    assert response["pipelines"]["multimodal_rag"]["status"] == "queryable"


def test_document_ingest_then_status_reports_queryable_for_usable_partial_pipelines(
    monkeypatch,
) -> None:
    module = _load_orchestrator_module()

    class FakeAsyncClient:
        posts: list[str] = []
        gets: list[str] = []

        def __init__(self, timeout: float | None = None) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            files: dict[str, Any],
            params: dict[str, Any],
        ) -> _FakeResponse:
            self.posts.append(url)
            if "8001" in url:
                return _FakeResponse(
                    {
                        "document_id": params["document_id"],
                        "filename": "manual.pdf",
                        "status": "processing",
                        "chunk_count": 0,
                    }
                )
            return _FakeResponse(
                {
                    "document_id": params["document_id"],
                    "filename": "manual.pdf",
                    "status": "partial",
                    "version": "multimodal-rag-v1:abc123",
                    "page_count": 3,
                    "rendered_pages": 2,
                }
            )

        async def get(self, url: str) -> _FakeResponse:
            self.gets.append(url)
            if "8001" in url:
                return _FakeResponse(
                    {
                        "status": "partial",
                        "chunk_count": 9,
                        "converted_text_available": True,
                        "warnings": [{"page": 1, "warning": "native_text_used"}],
                    }
                )
            return _FakeResponse(
                {
                    "status": "partial",
                    "version": "multimodal-rag-v1:abc123",
                    "page_count": 3,
                    "rendered_pages": 2,
                    "warnings": ["Page 3 rendered as text-only."],
                }
            )

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(module.app)

    ingest = client.post(
        "/documents/ingest",
        files={"file": ("manual.pdf", b"%PDF-1.4\nfake", "application/pdf")},
    )
    assert ingest.status_code == 200
    document_id = ingest.json()["document_id"]

    status = client.get(f"/documents/{document_id}/status")

    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "queryable"
    assert payload["pipelines"]["text_rag"]["status"] == "queryable"
    assert payload["pipelines"]["text_rag"]["chunk_count"] == 9
    assert payload["pipelines"]["multimodal_rag"]["status"] == "queryable"
    assert payload["pipelines"]["multimodal_rag"]["page_count"] == 3
    assert len(FakeAsyncClient.posts) == 2
    assert len(FakeAsyncClient.gets) == 2
