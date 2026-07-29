from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from .retrieval import RetrievedPage
from .storage import OriginalPdfStore


@dataclass(frozen=True)
class AnswerResult:
    text: str
    model_knowledge_used: bool


class Answerer(Protocol):
    name: str

    async def answer(
        self,
        question: str,
        evidence: list[RetrievedPage],
        allow_model_knowledge: bool,
        conversation: list[dict[str, str]] | None = None,
        model: str | None = None,
    ) -> AnswerResult: ...


def excerpt(text: str, limit: int = 500) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


class DeterministicOfflineAnswerer:
    name = "deterministic-offline-v1"

    async def answer(
        self,
        question: str,
        evidence: list[RetrievedPage],
        allow_model_knowledge: bool,
        conversation: list[dict[str, str]] | None = None,
        model: str | None = None,
    ) -> AnswerResult:
        del question, allow_model_knowledge, conversation, model
        if not evidence:
            return AnswerResult(
                text="I could not find relevant evidence in the selected manual.",
                model_knowledge_used=False,
            )
        statements = [
            f"{excerpt(item.page.text, 360) or 'The referenced page is visual.'} [C{index}]"
            for index, item in enumerate(evidence, start=1)
        ]
        return AnswerResult(text="\n\n".join(statements), model_knowledge_used=False)


class OpenRouterVisualAnswerer:
    name = "openrouter-visual-v1"

    def __init__(
        self,
        store: OriginalPdfStore,
        api_key: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self.store = store
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def answer(
        self,
        question: str,
        evidence: list[RetrievedPage],
        allow_model_knowledge: bool,
        conversation: list[dict[str, str]] | None = None,
        model: str | None = None,
    ) -> AnswerResult:
        evidence_lines = [
            f"[C{index}] {item.manifest.filename}, page {item.page.page}: "
            f"{excerpt(item.page.text, 1800) or '(visual page; inspect the attached image)'}"
            for index, item in enumerate(evidence, start=1)
        ]
        policy = (
            "You may use internal knowledge only when the evidence is insufficient, and "
            "must then set model_knowledge_used to true."
            if allow_model_knowledge
            else "Use only supplied evidence. If it is insufficient, say so and set "
            "model_knowledge_used to false."
        )
        history = "\n".join(
            f"{turn.get('role', 'unknown')}: {turn.get('content', '')}"
            for turn in (conversation or [])[-12:]
        )
        prompt = (
            "Answer the user's manual question. Cite document claims inline using only "
            "[C1], [C2], etc. Never invent a citation. "
            f"{policy}\n\nConversation:\n{history or '(none)'}"
            f"\n\nQuestion: {question}\n\nEvidence:\n"
            + "\n\n".join(evidence_lines)
            + '\n\nReturn JSON only: {"answer":"...", "model_knowledge_used":false}'
        )
        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for item in evidence:
            image = self.store.image_file(
                item.manifest.document_id,
                item.page.image_path,
            )
            if image:
                encoded = base64.b64encode(image.read_bytes()).decode("ascii")
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    }
                )
        request = {
            "model": model or self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": os.getenv(
                        "OPENROUTER_HTTP_REFERER", "http://localhost:3000"
                    ),
                    "X-Title": os.getenv("OPENROUTER_APP_TITLE", "OperatorOS"),
                },
                json=request,
            )
            response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return AnswerResult(text=str(raw).strip(), model_knowledge_used=False)
        parsed = json.loads(match.group(0))
        return AnswerResult(
            text=str(parsed.get("answer", "")).strip(),
            model_knowledge_used=bool(parsed.get("model_knowledge_used", False))
            and allow_model_knowledge,
        )


def build_answerer(store: OriginalPdfStore) -> Answerer:
    mode = os.getenv("MULTIMODAL_RAG_ANSWERER", "auto").strip().lower()
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if mode not in {"auto", "offline", "openrouter"}:
        raise RuntimeError(
            "MULTIMODAL_RAG_ANSWERER must be 'auto', 'offline', or 'openrouter'."
        )
    if mode == "openrouter" and not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is required when MULTIMODAL_RAG_ANSWERER=openrouter."
        )
    if mode == "openrouter" or (mode == "auto" and api_key):
        return OpenRouterVisualAnswerer(
            store=store,
            api_key=api_key,
            model=os.getenv(
                "MULTIMODAL_RAG_OPENROUTER_MODEL",
                "google/gemini-2.5-flash",
            ),
            timeout_seconds=float(
                os.getenv("MULTIMODAL_RAG_OPENROUTER_TIMEOUT_SECONDS", "120")
            ),
        )
    return DeterministicOfflineAnswerer()
