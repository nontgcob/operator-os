from __future__ import annotations

import base64
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from .models import DocumentManifest, PageRecord
from .storage import OriginalPdfStore


TOKEN_PATTERN = re.compile(r"[\w'-]+", re.UNICODE)


def _tokens(value: str) -> list[str]:
    return [token.casefold() for token in TOKEN_PATTERN.findall(value) if len(token) > 1]


@dataclass(frozen=True)
class RetrievedPage:
    manifest: DocumentManifest
    page: PageRecord
    score: float


class PageRetriever(Protocol):
    name: str

    def retrieve(
        self,
        question: str,
        document_ids: list[str],
        top_k: int,
    ) -> list[RetrievedPage]: ...


class DeterministicPageRetriever:
    """Local page adapter used until a ColPali/ColQwen visual adapter is configured.

    The independent index keeps page images beside page text, so this protocol can be
    replaced with a learned visual retriever without changing the service API.
    """

    name = "deterministic-page-index-v1"

    def __init__(self, store: OriginalPdfStore) -> None:
        self.store = store

    def retrieve(
        self,
        question: str,
        document_ids: list[str],
        top_k: int,
    ) -> list[RetrievedPage]:
        manifests = [self.store.get(document_id) for document_id in document_ids]
        candidates = [
            (manifest, page)
            for manifest in manifests
            for page in manifest.pages
            if page.text.strip() or page.image_path
        ]
        if not candidates:
            return []
        query = Counter(_tokens(question))
        if not query:
            return []
        document_frequency = Counter(
            token for _, page in candidates for token in set(_tokens(page.text))
        )
        total = len(candidates)
        scored: list[RetrievedPage] = []
        for manifest, page in candidates:
            page_terms = Counter(_tokens(page.text))
            score = 0.0
            for term, query_count in query.items():
                if term not in page_terms:
                    continue
                inverse_frequency = math.log((total + 1) / (document_frequency[term] + 1)) + 1
                score += query_count * (1 + math.log(page_terms[term])) * inverse_frequency
            if score > 0:
                scored.append(RetrievedPage(manifest=manifest, page=page, score=score))
        scored.sort(
            key=lambda item: (-item.score, item.manifest.document_id, item.page.page)
        )
        if scored:
            return scored[:top_k]
        # A scanned manual may have no extractable text. Passing rendered pages to
        # the visual answerer is safer than incorrectly treating it as empty.
        return [
            RetrievedPage(manifest=manifest, page=page, score=0.0)
            for manifest, page in candidates
            if page.image_path
        ][:top_k]


class OpenRouterVisualPageRetriever:
    """Ranks original rendered pages by inspecting page images in bounded batches."""

    name = "openrouter-visual-page-ranker-v1"

    def __init__(
        self,
        store: OriginalPdfStore,
        *,
        api_key: str,
        model: str,
        batch_size: int,
        timeout_seconds: float,
    ) -> None:
        self.store = store
        self.api_key = api_key
        self.model = model
        self.batch_size = max(1, min(batch_size, 12))
        self.timeout_seconds = timeout_seconds
        self.fallback = DeterministicPageRetriever(store)

    def _score_batch(
        self,
        question: str,
        batch: list[tuple[DocumentManifest, PageRecord]],
    ) -> list[float]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Rank each original manual page for its relevance to the user's question. "
                    "Inspect diagrams, arrows, labels, screenshots, tables, warnings, layout, "
                    "and text. Return JSON only as "
                    '{"scores":[{"candidate":1,"score":0.0}]} with one score from 0 to 10 '
                    "for every candidate.\n\n"
                    f"Question: {question}"
                ),
            }
        ]
        for index, (manifest, page) in enumerate(batch, start=1):
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"Candidate {index}: {manifest.filename}, page {page.page}\n"
                        f"Extracted text: {page.text[:1200] or '(no extractable text)'}"
                    ),
                }
            )
            image = self.store.image_file(manifest.document_id, page.image_path)
            if image:
                encoded = base64.b64encode(image.read_bytes()).decode("ascii")
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    }
                )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": os.getenv(
                        "OPENROUTER_HTTP_REFERER", "http://localhost:3000"
                    ),
                    "X-Title": os.getenv("OPENROUTER_APP_TITLE", "OperatorOS"),
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": 0,
                },
            )
            response.raise_for_status()
        raw = str(response.json()["choices"][0]["message"]["content"])
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("visual ranker returned no JSON")
        payload = json.loads(match.group(0))
        scores = [0.0] * len(batch)
        for item in payload.get("scores", []):
            candidate = int(item.get("candidate", 0))
            if 1 <= candidate <= len(batch):
                scores[candidate - 1] = max(0.0, min(float(item.get("score", 0)), 10.0))
        return scores

    def retrieve(
        self,
        question: str,
        document_ids: list[str],
        top_k: int,
    ) -> list[RetrievedPage]:
        manifests = [self.store.get(document_id) for document_id in document_ids]
        candidates = [
            (manifest, page)
            for manifest in manifests
            for page in manifest.pages
            if page.image_path or page.text.strip()
        ]
        if not candidates:
            return []
        scored: list[RetrievedPage] = []
        try:
            for start in range(0, len(candidates), self.batch_size):
                batch = candidates[start : start + self.batch_size]
                scores = self._score_batch(question, batch)
                scored.extend(
                    RetrievedPage(manifest=manifest, page=page, score=score)
                    for (manifest, page), score in zip(batch, scores, strict=True)
                )
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self.fallback.retrieve(question, document_ids, top_k)
        scored.sort(
            key=lambda item: (-item.score, item.manifest.document_id, item.page.page)
        )
        return scored[:top_k]


def build_retriever(store: OriginalPdfStore) -> PageRetriever:
    adapter = os.getenv("MULTIMODAL_RAG_RETRIEVER", "auto").strip().lower()
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if adapter in {"openrouter", "auto"} and api_key:
        return OpenRouterVisualPageRetriever(
            store,
            api_key=api_key,
            model=os.getenv(
                "MULTIMODAL_RAG_RETRIEVER_MODEL",
                "qwen/qwen3-vl-8b-instruct",
            ),
            batch_size=int(os.getenv("MULTIMODAL_RAG_RETRIEVER_BATCH_SIZE", "6")),
            timeout_seconds=float(
                os.getenv("MULTIMODAL_RAG_RETRIEVER_TIMEOUT_SECONDS", "120")
            ),
        )
    if adapter not in {"auto", "deterministic"}:
        raise RuntimeError(
            "MULTIMODAL_RAG_RETRIEVER must be 'auto', 'openrouter', or 'deterministic'."
        )
    return DeterministicPageRetriever(store)
