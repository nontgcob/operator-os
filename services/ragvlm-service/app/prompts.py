SYSTEM_PROMPT = """You are OperatorOS, an industrial multimodal assistant.
- Ground responses in visible frame details.
- Use annotation hints as spatial references.
- Use transcript and manual excerpts as temporal/document evidence.
- If information is uncertain, state uncertainty explicitly.
"""


def build_prompt(question: str, annotations: str, transcript: str, docs: str) -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Question:\n{question}\n\n"
        f"Annotations:\n{annotations}\n\n"
        f"Transcript window:\n{transcript}\n\n"
        f"Manual excerpts:\n{docs}"
    )
