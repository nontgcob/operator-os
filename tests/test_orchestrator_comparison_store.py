from __future__ import annotations

from pathlib import Path
import sys

import pytest

ORCH_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "orchestrator" / "app"
sys.path.append(str(ORCH_APP_PATH))
from comparison_store import ComparisonStore  # type: ignore  # noqa: E402


def test_comparison_store_keeps_mapping_hidden_until_caller_reveals(tmp_path: Path) -> None:
    store = ComparisonStore(tmp_path / "comparisons.sqlite3")
    created = store.create(
        comparison_id="cmp-1",
        session_id="session-1",
        question="Where is the filter?",
        mapping={"A": "text_rag", "B": "multimodal_rag"},
        request={"document_ids": ["doc-1"]},
    )

    assert created["status"] == "running"
    assert created["selected_label"] is None

    store.record_answer("cmp-1", label="A", answer={"status": "completed", "text": "A"})
    completed = store.record_answer("cmp-1", label="B", answer={"status": "completed", "text": "B"})
    assert completed["status"] == "completed"

    revealed = store.reveal("cmp-1", "B")
    assert revealed["selected_label"] == "B"
    assert revealed["mapping"]["B"] == "multimodal_rag"

    # Repeating the same reveal is idempotent.
    assert store.reveal("cmp-1", "B")["selected_label"] == "B"
    with pytest.raises(ValueError, match="cannot be changed"):
        store.reveal("cmp-1", "A")


def test_comparison_store_disables_reveal_for_partial_results(tmp_path: Path) -> None:
    store = ComparisonStore(tmp_path / "comparisons.sqlite3")
    store.create(
        comparison_id="cmp-2",
        session_id="session-1",
        question="Question",
        mapping={"A": "multimodal_rag", "B": "text_rag"},
        request={},
    )
    store.record_answer("cmp-2", label="A", answer={"status": "completed", "text": "answer"})
    record = store.record_answer("cmp-2", label="B", answer={"status": "error", "error": "timeout"})

    assert record["status"] == "partial"
    with pytest.raises(ValueError, match="only completed"):
        store.reveal("cmp-2", "A")


def test_comparison_store_counts_left_side_pipeline_assignments(tmp_path: Path) -> None:
    store = ComparisonStore(tmp_path / "comparisons.sqlite3")
    store.create(
        comparison_id="cmp-1",
        session_id="session-1",
        question="Question",
        mapping={"A": "multimodal_rag", "B": "text_rag"},
        request={},
    )
    store.create(
        comparison_id="cmp-2",
        session_id="session-1",
        question="Question",
        mapping={"A": "text_rag", "B": "multimodal_rag"},
        request={},
    )
    store.create(
        comparison_id="cmp-3",
        session_id="session-1",
        question="Question",
        mapping={"A": "multimodal_rag", "B": "text_rag"},
        request={},
    )

    store.create(
        comparison_id="cmp-4",
        session_id="other-session",
        question="Question",
        mapping={"A": "text_rag", "B": "multimodal_rag"},
        request={},
    )

    assert store.mapping_counts("session-1") == {"text_left": 1, "multimodal_left": 2}
    assert store.mapping_counts("other-session") == {"text_left": 1, "multimodal_left": 0}
