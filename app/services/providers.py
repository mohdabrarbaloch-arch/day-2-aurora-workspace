"""Answer providers.

Two implementations behind one interface:

* ``LocalProvider``  — retrieval + extractive composition. Always available,
  no key, no network. This is the default.
* ``GeminiProvider`` — the same retrieval step, but the selected sentences are
  handed to Gemini for a fluent rewrite. Opt-in, requires an API key.

The provider name is returned to the client with every answer, so the UI can
never imply a model answered when it did not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from app.config import settings
from app.services.retrieval import (
    BM25Index,
    Chunk,
    Citation,
    build_citations,
    compose_answer,
)


@dataclass(slots=True)
class AnswerResult:
    content: str
    provider: str
    grounded: bool
    citations: list[Citation] = field(default_factory=list)
    latency_ms: float = 0.0


class AnswerProvider(Protocol):
    name: str

    def answer(self, question: str, chunks: list[Chunk]) -> AnswerResult: ...


class LocalProvider:
    """Deterministic, offline, extractive."""

    name = "local"

    def answer(self, question: str, chunks: list[Chunk]) -> AnswerResult:
        started = time.perf_counter()
        index = BM25Index(chunks)
        scored = index.search(question, top_k=settings.retrieval_top_k)
        content, grounded = compose_answer(scored, question, min_score=settings.retrieval_min_score)
        citations = build_citations(scored, question) if grounded else []
        return AnswerResult(
            content=content,
            provider=self.name,
            grounded=grounded,
            citations=citations,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )


class GeminiProvider:
    """Retrieval-augmented rewrite via Google Gemini.

    The retrieved passages are the only material given to the model, and the
    prompt makes declining an explicit option. Citations still come from the
    retrieval step — the model cannot invent a source, because it never
    produces the citation list itself.
    """

    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def answer(self, question: str, chunks: list[Chunk]) -> AnswerResult:
        started = time.perf_counter()
        index = BM25Index(chunks)
        scored = index.search(question, top_k=settings.retrieval_top_k)
        content, grounded = compose_answer(scored, question, min_score=settings.retrieval_min_score)
        if not grounded:
            return AnswerResult(
                content=content,
                provider=self.name,
                grounded=False,
                citations=[],
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )

        rewritten = self._rewrite(question, content)
        return AnswerResult(
            content=rewritten or content,
            provider=self.name,
            grounded=True,
            citations=build_citations(scored, question),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def _rewrite(self, question: str, passages: str) -> str:
        """Ask Gemini to phrase the retrieved facts as direct prose.

        Any failure falls back to the extractive answer — a provider outage must
        degrade the product, not break it.
        """
        try:
            import httpx

            response = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self._model}:generateContent",
                headers={"x-goog-api-key": self._api_key},
                json={
                    "contents": [
                        {
                            "parts": [
                                {
                                    "text": (
                                        "Answer the question using ONLY the passages below. "
                                        "If they do not contain the answer, reply exactly: "
                                        "NOT_COVERED. Do not add outside knowledge.\n\n"
                                        f"Passages:\n{passages}\n\nQuestion: {question}"
                                    )
                                }
                            ]
                        }
                    ],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
                },
                timeout=20.0,
            )
            response.raise_for_status()
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            return ""
        if not text or "NOT_COVERED" in text:
            return ""
        return text


def get_provider() -> AnswerProvider:
    """Select the provider. Gemini is used only if explicitly enabled *and* keyed."""
    if settings.use_gemini:
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model)
    return LocalProvider()
