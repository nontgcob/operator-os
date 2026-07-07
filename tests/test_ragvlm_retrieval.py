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
    words = " ".join(f"word{idx}" for idx in range(25))
    chunks = chunk_text(words, chunk_size=10, overlap=2)

    assert len(chunks) == 3
    assert chunks[0].split()[-2:] == chunks[1].split()[:2]
    assert chunks[1].split()[-2:] == chunks[2].split()[:2]


def test_retrieve_chunks_ranks_matching_document(tmp_path: Path) -> None:
    configure_document_dir(tmp_path)
    ingest_document_text(
        "Torque wrench calibration requires setting the lower valve before measurement.",
        filename="calibration.txt",
        document_id="calibration",
        chunk_size=8,
        overlap=2,
    )
    ingest_document_text(
        "General cleaning procedure covers gloves and surface wipe down.",
        filename="cleaning.txt",
        document_id="cleaning",
        chunk_size=8,
        overlap=2,
    )

    chunks = retrieve_chunks(
        "How should I calibrate the torque wrench?",
        ["calibration", "cleaning"],
        top_k=2,
    )

    assert chunks[0]["document_id"] == "calibration"
    assert "Torque wrench calibration" in chunks[0]["text"]
    assert chunks[0]["score"] > chunks[1]["score"]
