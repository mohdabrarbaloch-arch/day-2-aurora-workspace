"""Retrieval engine: chunking, BM25 scoring, sentence ranking, citations.

This is the part of Aurora that decides what the answer is *made of*. It is
deliberately dependency-free so it can be unit tested deterministically — no
network, no model, no flakiness.

Design note: the answer is **extractive**. Sentences are selected from the
retrieved chunks and stitched together, never paraphrased. That is what makes
"the model can't make things up" a structural property rather than a hope.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

# Tokens: words, numbers, and dotted identifiers such as `app.config`.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")

# Sentence boundaries: terminator followed by whitespace/end. Deliberately
# conservative — we would rather under-split than cut a citation in half.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n{2,}")

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "by",
        "with",
        "without",
        "into",
        "over",
        "under",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "doing",
        "have",
        "has",
        "had",
        "having",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "its",
        "our",
        "their",
        "as",
        "not",
        "no",
        "nor",
        "so",
        "such",
        "too",
        "very",
        "can",
        "could",
        "should",
        "would",
        "will",
        "shall",
        "may",
        "might",
        "must",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "there",
        "here",
        "about",
        "above",
        "after",
        "again",
        "against",
        "before",
        "below",
        "between",
        "during",
    ]
)

CHUNK_SIZE = 900  # characters; ~150 words, fits comfortably under a page
CHUNK_OVERLAP = 150


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens with stopwords removed.

    Identifiers keep their dots so `os.environ` does not degrade into `os` +
    `environ`, which would match unrelated text.
    """
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if t not in STOPWORDS]


@dataclass(slots=True)
class Chunk:
    """A retrievable slice of an asset."""

    asset_id: str
    filename: str
    index: int
    text: str
    page: int | None = None
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = tokenize(self.text)


@dataclass(slots=True)
class ScoredChunk:
    chunk: Chunk
    score: float


@dataclass(slots=True)
class Citation:
    asset_id: str
    filename: str
    page: int | None
    snippet: str
    score: float


def chunk_text(
    text: str,
    *,
    asset_id: str,
    filename: str,
    page: int | None = None,
    size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[Chunk]:
    """Split text into overlapping chunks on word boundaries.

    Overlap exists so a sentence spanning a boundary is still fully present in
    at least one chunk — otherwise a citation can point at half a fact.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        return []
    if len(cleaned) <= size:
        return [Chunk(asset_id, filename, 0, cleaned, page)]

    words = cleaned.split(" ")
    chunks: list[Chunk] = []
    cursor = 0
    index = 0
    step_back = max(1, overlap // 6)  # overlap measured in words

    while cursor < len(words):
        window = words[cursor : cursor + size // 6]
        if not window:
            break
        chunks.append(Chunk(asset_id, filename, index, " ".join(window), page))
        index += 1
        if cursor + size // 6 >= len(words):
            break
        cursor += max(1, (size // 6) - step_back)
    return chunks


class BM25Index:
    """Okapi BM25 over a fixed corpus of chunks.

    k1 controls term-frequency saturation, b controls length normalisation.
    The defaults (1.5 / 0.75) are the standard literature values and are fine
    for short documents; neither needed retuning here.
    """

    def __init__(self, chunks: list[Chunk], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self._doc_freq: Counter[str] = Counter()
        self._doc_len: list[int] = []
        for chunk in chunks:
            unique = set(chunk.tokens)
            self._doc_freq.update(unique)
            self._doc_len.append(len(chunk.tokens))
        self._n_docs = len(chunks)
        self._avg_len = (sum(self._doc_len) / self._n_docs) if self._n_docs else 0.0

    def idf(self, term: str) -> float:
        """BM25 inverse document frequency with the +0.5 smoothing guard.

        The `max(0.0, ...)` clamp stops a term present in every chunk from
        contributing a negative score, which would otherwise punish the very
        documents that match the query.
        """
        df = self._doc_freq.get(term, 0)
        if self._n_docs == 0:
            return 0.0
        raw = math.log(1 + (self._n_docs - df + 0.5) / (df + 0.5))
        return max(0.0, raw)

    def score(self, query_tokens: list[str], doc_index: int) -> float:
        if not query_tokens or not self._n_docs:
            return 0.0
        chunk = self.chunks[doc_index]
        freq = Counter(chunk.tokens)
        doc_len = self._doc_len[doc_index] or 1
        total = 0.0
        for term in set(query_tokens):
            tf = freq.get(term, 0)
            if tf == 0:
                continue
            denom = tf + self.k1 * (1 - self.b + self.b * (doc_len / (self._avg_len or 1)))
            total += self.idf(term) * (tf * (self.k1 + 1)) / (denom or 1)
        return total

    def search(self, query: str, *, top_k: int = 3) -> list[ScoredChunk]:
        query_tokens = tokenize(query)
        if not query_tokens or not self.chunks:
            return []
        scored = [
            ScoredChunk(chunk=self.chunks[i], score=self.score(query_tokens, i))
            for i in range(self._n_docs)
        ]
        scored = [s for s in scored if s.score > 0]
        scored.sort(key=lambda s: (-s.score, s.chunk.asset_id, s.chunk.index))
        return scored[:top_k]


def split_sentences(text: str) -> list[str]:
    """Split a chunk into sentences, keeping fragments that are still useful."""
    parts = [p.strip() for p in _SENTENCE_RE.split(text) if p.strip()]
    return [p for p in parts if len(p) > 2]


def rank_sentences(sentences: list[str], query: str, *, limit: int = 3) -> list[str]:
    """Order sentences by how much of the query they cover.

    Coverage (fraction of distinct query terms present) is used ahead of raw
    count so a sentence matching three of five query terms outranks one that
    repeats a single term three times.
    """
    query_terms = set(tokenize(query))
    if not query_terms or not sentences:
        return sentences[:limit]

    ranked: list[tuple[float, int, str]] = []
    for position, sentence in enumerate(sentences):
        tokens = set(tokenize(sentence))
        if not tokens:
            continue
        covered = len(query_terms & tokens)
        if covered == 0:
            continue
        coverage = covered / len(query_terms)
        # Earlier sentences break ties: in prose the topic sentence comes first.
        ranked.append((coverage, -position, sentence))

    ranked.sort(key=lambda r: (-r[0], -r[1]))
    return [sentence for _, _, sentence in ranked[:limit]]


def build_citations(
    scored: list[ScoredChunk], query: str, *, snippet_chars: int = 260
) -> list[Citation]:
    """Turn scored chunks into citation cards for the UI."""
    citations: list[Citation] = []
    for item in scored:
        best = rank_sentences(split_sentences(item.chunk.text), query, limit=1)
        snippet = best[0] if best else item.chunk.text
        if len(snippet) > snippet_chars:
            snippet = snippet[:snippet_chars].rstrip() + "…"
        citations.append(
            Citation(
                asset_id=item.chunk.asset_id,
                filename=item.chunk.filename,
                page=item.chunk.page,
                snippet=snippet,
                score=round(item.score, 4),
            )
        )
    return citations


def select_with_context(sentences: list[str], query: str, *, limit: int = 2) -> list[str]:
    """Pick the best-matching sentences and pull in the sentence that follows.

    A single matched sentence often ends exactly where the useful detail starts
    ("Afternoons reach the high thirties. Evenings cool down…"). Including the
    next sentence gives a complete thought without letting the answer sprawl.
    """
    ranked = rank_sentences(sentences, query, limit=limit)
    if not ranked:
        return []

    chosen: list[str] = []
    for sentence in ranked:
        try:
            position = sentences.index(sentence)
        except ValueError:
            chosen.append(sentence)
            continue
        chosen.append(sentence)
        follower = position + 1
        if follower < len(sentences):
            nxt = sentences[follower]
            if nxt not in chosen and nxt not in ranked:
                chosen.append(nxt)
    return chosen[:limit]


def compose_answer(
    scored: list[ScoredChunk],
    query: str,
    *,
    min_score: float,
    max_sentences: int = 4,
) -> tuple[str, bool]:
    """Build an extractive answer from retrieved chunks.

    Returns ``(answer, grounded)``. When nothing clears ``min_score`` the answer
    explicitly declines — this is the single most important behaviour in the
    product, so it is a pure function that can be asserted on directly.
    """
    usable = [s for s in scored if s.score >= min_score]
    if not usable:
        return (
            "I couldn't find anything in your uploaded files that answers that. "
            "Try rephrasing, or add the document or image that covers it.",
            False,
        )

    sentences: list[str] = []
    seen: set[str] = set()
    for item in usable:
        for sentence in select_with_context(split_sentences(item.chunk.text), query, limit=2):
            key = sentence.lower()[:120]
            if key not in seen:
                seen.add(key)
                sentences.append(sentence)
            if len(sentences) >= max_sentences:
                break
        if len(sentences) >= max_sentences:
            break

    if not sentences:
        # Chunks scored above threshold but held no complete sentence — fall
        # back to the chunk text so the user still sees the supporting source.
        sentences = [usable[0].chunk.text[:400]]

    return " ".join(sentences).strip(), True
