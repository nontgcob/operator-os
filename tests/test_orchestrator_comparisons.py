from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient


def _load_orchestrator_module(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RAG_COMPARISON_DB_PATH", str(tmp_path / "comparisons.sqlite3"))
    monkeypatch.setenv("CHAT_LOG_PATH", str(tmp_path / "chat.jsonl"))
    module_path = Path(__file__).resolve().parents[1] / "services" / "orchestrator" / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("orchestrator_main_comparisons", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _request_body() -> dict[str, Any]:
    return {
        "session_id": "session-1",
        "question": "Where is the filter?",
        "document_ids": ["manual-1"],
    }


def _events(response_text: str) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for block in response_text.strip().split("\n\n"):
        event = "message"
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            if line.startswith("data: "):
                data.append(line[6:])
        if data:
            result.append((event, json.loads("\n".join(data))))
    return result


def test_comparison_stream_keeps_pipeline_mapping_out_of_client_payload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_orchestrator_module(monkeypatch, tmp_path)
    monkeypatch.setattr(
        module,
        "_new_blinded_mapping",
        lambda session_id: {"A": "text_rag", "B": "multimodal_rag"},
    )

    async def fake_pipeline(pipeline: str, payload: Any) -> dict[str, Any]:
        return {
            "answer_id": "opaque-answer-id",
            "status": "completed",
            "text": f"Grounded answer for {payload.question}",
            "provenance": "document",
            "citations": [
                {
                    "citation_id": "e1",
                    "source_kind": "document",
                    "filename": "manual.pdf",
                    "page_number": 2,
                }
            ],
        }

    monkeypatch.setattr(module, "_request_comparison_pipeline", fake_pipeline)
    client = TestClient(module.app)

    response = client.post("/chat/comparisons/stream", json=_request_body())

    assert response.status_code == 200
    events = _events(response.text)
    assert events[0][0] == "comparison_started"
    assert events[-1] == (
        "comparison_complete",
        {
            "comparison_id": events[0][1]["comparison_id"],
            "status": "completed",
        },
    )
    assert "text_rag" not in response.text
    assert "multimodal_rag" not in response.text
    labels = {
        payload["label"]
        for event, payload in events
        if event == "answer_complete"
    }
    assert labels == {"A", "B"}

    comparison_id = events[0][1]["comparison_id"]
    reveal = client.post(
        f"/chat/comparisons/{comparison_id}/reveal",
        json={"selected_label": "A"},
    )
    assert reveal.status_code == 200
    assert reveal.json()["mapping"] == {"A": "text_rag", "B": "multimodal_rag"}


def test_partial_comparison_cannot_be_revealed(monkeypatch, tmp_path: Path) -> None:
    module = _load_orchestrator_module(monkeypatch, tmp_path)

    async def fake_pipeline(pipeline: str, payload: Any) -> dict[str, Any]:
        if pipeline == "multimodal_rag":
            raise RuntimeError("timed out")
        return {
            "status": "completed",
            "text": "Text answer",
            "provenance": "document",
            "citations": [],
        }

    monkeypatch.setattr(module, "_request_comparison_pipeline", fake_pipeline)
    client = TestClient(module.app)

    response = client.post("/chat/comparisons/stream", json=_request_body())
    events = _events(response.text)
    comparison_id = events[0][1]["comparison_id"]

    assert events[-1][1]["status"] == "partial"
    reveal = client.post(
        f"/chat/comparisons/{comparison_id}/reveal",
        json={"selected_label": "A"},
    )
    assert reveal.status_code == 409


def test_blinded_mapping_balances_persisted_left_side_assignments(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_orchestrator_module(monkeypatch, tmp_path)
    monkeypatch.setattr(module.secrets, "randbelow", lambda _: 1)

    first = module._new_blinded_mapping("session-1")
    assert first == {"A": "multimodal_rag", "B": "text_rag"}
    module.comparison_store.create(
        comparison_id="cmp-1",
        session_id="session-1",
        question="Question",
        mapping=first,
        request={},
    )

    second = module._new_blinded_mapping("session-1")
    assert second == {"A": "text_rag", "B": "multimodal_rag"}


def test_blinded_mapping_is_balanced_per_session_not_globally(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_orchestrator_module(monkeypatch, tmp_path)
    monkeypatch.setattr(module.secrets, "randbelow", lambda _: 0)
    module.comparison_store.create(
        comparison_id="cmp-other-1",
        session_id="other-session",
        question="Question",
        mapping={"A": "multimodal_rag", "B": "text_rag"},
        request={},
    )
    module.comparison_store.create(
        comparison_id="cmp-other-2",
        session_id="other-session",
        question="Question",
        mapping={"A": "multimodal_rag", "B": "text_rag"},
        request={},
    )

    assert module._new_blinded_mapping("new-session") == {
        "A": "text_rag",
        "B": "multimodal_rag",
    }

    module.comparison_store.create(
        comparison_id="cmp-new-1",
        session_id="new-session",
        question="Question",
        mapping={"A": "text_rag", "B": "multimodal_rag"},
        request={},
    )
    assert module._new_blinded_mapping("new-session") == {
        "A": "multimodal_rag",
        "B": "text_rag",
    }


def test_comparison_pipeline_requests_are_document_grounded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_orchestrator_module(monkeypatch, tmp_path)
    captured: list[dict[str, Any]] = []

    class FakeAsyncClient:
        def __init__(self, timeout: float | None = None) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
            captured.append({"url": url, "json": json})
            return httpx.Response(
                200,
                json={
                    "status": "complete",
                    "text": "Use the rear filter.",
                    "provenance": "document",
                    "citations": [],
                },
            )

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)

    payload = module.ComparisonStreamRequest(**_request_body())
    result = module.asyncio.run(module._request_comparison_pipeline("text_rag", payload))

    assert result["status"] == "completed"
    assert captured[0]["url"].endswith("/rag/text/answer")
    assert captured[0]["json"]["document_ids"] == ["manual-1"]
    assert captured[0]["json"]["allow_model_knowledge"] is False
