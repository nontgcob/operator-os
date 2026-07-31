from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


def _load_orchestrator_module(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RAG_COMPARISON_DB_PATH", str(tmp_path / "comparisons.sqlite3"))
    module_path = Path(__file__).resolve().parents[1] / "services" / "orchestrator" / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("orchestrator_main_comparisons", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_legacy_rag_comparison_endpoint_is_gone(monkeypatch, tmp_path: Path) -> None:
    module = _load_orchestrator_module(tmp_path, monkeypatch)
    client = TestClient(module.app)

    response = client.post(
        "/chat/comparisons/stream",
        json={
            "session_id": "s1",
            "question": "What does the manual say?",
            "document_ids": ["manual"],
        },
    )

    assert response.status_code == 410
    assert "removed" in response.json()["detail"]
