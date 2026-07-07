from __future__ import annotations

import sys
from pathlib import Path

RAGVLM_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "ragvlm-service" / "app"
sys.path.append(str(RAGVLM_APP_PATH))

from rag.retrieval import (  # type: ignore  # noqa: E402
    chunk_text,
    configure_document_dir,
    ingest_document_text,
    retrieve_chunks,
)


def test_chunk_text_overlaps_adjacent_chunks() -> None:
    text = "abcdefghij" "klmnopqrst" "uvwxyz"
    chunks = chunk_text(text, chunk_size=10, overlap=2)

    assert len(chunks) == 3
    assert chunks[0][-2:] == chunks[1][:2]
    assert chunks[1][-2:] == chunks[2][:2]


def test_retrieve_chunks_includes_small_attached_documents_in_order(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)
    ingest_document_text(
        "Torque wrench calibration requires setting the lower valve before measurement.",
        filename="calibration.txt",
        document_id="calibration",
        chunk_size=80,
        overlap=2,
    )
    ingest_document_text(
        "General cleaning procedure covers gloves and surface wipe down.",
        filename="cleaning.txt",
        document_id="cleaning",
        chunk_size=80,
        overlap=2,
    )

    chunks = retrieve_chunks(
        "What documents are loaded?",
        ["calibration", "cleaning"],
        top_k=2,
    )

    assert chunks[0]["document_id"] == "calibration"
    assert "Torque wrench calibration" in chunks[0]["text"]
    assert chunks[0]["score"] == 1.0


def test_retrieve_chunks_uses_embedding_similarity_without_scope(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)

    def embedder(texts: list[str]) -> list[list[float]]:
        vectors = {
            "Torque wrench calibration requires setting the lower valve before measurement.": [1.0, 0.0],
            "General cleaning procedure covers gloves and surface wipe down.": [0.0, 1.0],
        }
        return [vectors[text] for text in texts]

    ingest_document_text(
        "Torque wrench calibration requires setting the lower valve before measurement.",
        filename="calibration.txt",
        document_id="calibration",
        embedder=embedder,
    )
    ingest_document_text(
        "General cleaning procedure covers gloves and surface wipe down.",
        filename="cleaning.txt",
        document_id="cleaning",
        embedder=embedder,
    )

    chunks = retrieve_chunks(
        "How should I calibrate the torque wrench?",
        top_k=2,
        query_embedder=lambda texts: [[1.0, 0.0] for _ in texts],
    )

    assert chunks[0]["document_id"] == "calibration"
    assert chunks[0]["score"] > chunks[1]["score"]
