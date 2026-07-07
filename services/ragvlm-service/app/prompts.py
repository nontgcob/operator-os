from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """You are OperatorOS, an industrial multimodal assistant adapted from RAGVLM.
- Ground every answer in the visible video frame first.
- Treat annotations as user intent signals in normalized RAGVLM 0-1000 image coordinates.
- Use transcript excerpts for temporal context and document excerpts for procedural evidence.
- Explain spatial relationships precisely when annotations are present.
- If the frame, transcript, or documents do not support an answer, say what is uncertain.
"""


def _format_annotations(annotations: list[dict[str, Any]] | str) -> str:
    if isinstance(annotations, str):
        return annotations
    if not annotations:
        return "No annotations."
    return json.dumps(annotations, ensure_ascii=False, indent=2)


def build_prompt(
    question: str,
    annotations: list[dict[str, Any]] | str,
    transcript: str,
    docs: str,
    *,
    model_family: str = "custom",
) -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Model family: {model_family}\n\n"
        f"Question:\n{question}\n\n"
        f"Normalized annotations:\n{_format_annotations(annotations)}\n\n"
        f"Transcript window:\n{transcript}\n\n"
        f"Retrieved document excerpts:\n{docs}\n\n"
        "Answer requirements:\n"
        "- Be concise but cite the visual, temporal, or document evidence you used.\n"
        "- Do not invent manual details that are absent from the retrieved excerpts.\n"
        "- When relevant, mention the annotated region using the normalized coordinate frame."
    )
