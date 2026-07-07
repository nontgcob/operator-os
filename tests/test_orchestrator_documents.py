from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any


def _load_orchestrator_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "orchestrator" / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("orchestrator_main_documents", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self) -> dict[str, Any]:
        return {
            "chunks": [
                {"text": "manual chunk one"},
                {"text": ""},
                {"not_text": "ignored"},
                {"text": "manual chunk two"},
            ]
        }


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
