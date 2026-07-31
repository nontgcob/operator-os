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
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = "" if status_code < 400 else str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


def test_document_retrieve_endpoint_is_gone() -> None:
    module = _load_orchestrator_module()
    client = TestClient(module.app)

    response = client.post("/documents/retrieve", json={"question": "x", "document_ids": ["manual"]})

    assert response.status_code == 410


def test_document_status_reports_single_direct_pdf_pipeline(monkeypatch) -> None:
    module = _load_orchestrator_module()

    async def fake_pipeline_status(url: str) -> dict[str, Any]:
        return {
            "document_id": "manual-1",
            "status": "queryable",
            "interaction_mode": "direct_pdf_vlm",
            "chunk_count": 0,
            "original_available": True,
        }

    monkeypatch.setattr(module, "_get_pipeline_status", fake_pipeline_status)

    response = asyncio.run(module.document_status("manual-1"))

    assert response["status"] == "queryable"
    assert set(response["pipelines"]) == {"direct_pdf_vlm"}
    assert response["pipelines"]["direct_pdf_vlm"]["interaction_mode"] == "direct_pdf_vlm"


def test_document_ingest_only_calls_ragvlm_service(monkeypatch) -> None:
    module = _load_orchestrator_module()

    class FakeAsyncClient:
        posts: list[str] = []

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
            return _FakeResponse(
                {
                    "document_id": params["document_id"],
                    "filename": "manual.pdf",
                    "status": "queryable",
                    "chunk_count": 0,
                    "interaction_mode": "direct_pdf_vlm",
                }
            )

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(module.app)

    ingest = client.post(
        "/documents/ingest",
        files={"file": ("manual.pdf", b"%PDF-1.4\nfake", "application/pdf")},
    )

    assert ingest.status_code == 200
    payload = ingest.json()
    assert payload["status"] == "queryable"
    assert payload["pipelines"]["direct_pdf_vlm"]["status"] == "queryable"
    assert FakeAsyncClient.posts == [f"{module.RAGVLM_SERVICE_URL}/documents/ingest"]
