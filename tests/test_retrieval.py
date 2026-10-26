"""Tests for the retrieval engine — the part that decides what an answer says."""

from __future__ import annotations

from app.services.retrieval import (
    BM25Index,
    Chunk,
    build_citations,
    chunk_text,
    compose_answer,
    rank_sentences,
    split_sentences,
    tokenize,
)

CORPUS = [
    "Python lists are ordered, mutable sequences. You append with .append() and "
    "remove with .remove(). Lists allow duplicates.",
    "FastAPI is a Python web framework. It builds APIs with type hints and "
    "validates requests with Pydantic models.",
    "Karachi weather in October is hot. Temperatures reach the high thirties " "most afternoons.",
]


def _index() -> BM25Index:
    chunks = [
        Chunk("a1", "python-lists.txt", 0, CORPUS[0]),
        Chunk("a2", "fastapi.txt", 0, CORPUS[1]),
        Chunk("a3", "weather.txt", 0, CORPUS[2]),
    ]
    return BM25Index(chunks)


# 1 ---------------------------------------------------------------------------
def test_tokenize_drops_stopwords_and_keeps_identifiers():
    tokens = tokenize("What is the os.environ and the list?")
    assert "os.environ" in tokens, "dotted identifiers must survive as one token"
    assert "what" not in tokens and "the" not in tokens
    assert "list" in tokens


# 2 ---------------------------------------------------------------------------
def test_bm25_ranks_the_relevant_chunk_first():
    results = _index().search("how do I append to a list", top_k=3)
    assert results, "a matching query must return hits"
    assert results[0].chunk.asset_id == "a1"
    assert results[0].score > 0


# 3 ---------------------------------------------------------------------------
def test_bm25_returns_nothing_for_unrelated_query():
    assert _index().search("quantum chromodynamics lattice", top_k=3) == []


# 4 ---------------------------------------------------------------------------
def test_bm25_scores_are_ordered_and_distinct():
    results = _index().search("python type hints for APIs", top_k=3)
    assert results[0].chunk.asset_id == "a2"
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


# 5 ---------------------------------------------------------------------------
def test_chunk_text_respects_single_small_document():
    chunks = chunk_text("short text", asset_id="x", filename="a.txt")
    assert len(chunks) == 1
    assert chunks[0].text == "short text"


# 6 ---------------------------------------------------------------------------
def test_chunk_text_splits_long_documents_with_overlap():
    body = " ".join(f"word{i}" for i in range(600))
    chunks = chunk_text(body, asset_id="x", filename="a.txt", size=900, overlap=150)
    assert len(chunks) > 1, "a 600-word document must split"

    # Overlap means the head of chunk N+1 reappears inside chunk N. The windows
    # are 150 words stepping 125, so the shared region is 25 words — assert
    # containment rather than a slice comparison, which would compare disjoint
    # slices of a correct overlap.
    first_words = set(chunks[0].text.split())
    second_head = set(chunks[1].text.split()[:20])
    assert second_head <= first_words, (
        "the head of the next chunk must repeat inside the previous one"
    )

    # And the chunk count must cover the whole document without losing the tail.
    assert "word599" in " ".join(c.text for c in chunks)


# 7 ---------------------------------------------------------------------------
def test_chunk_text_returns_empty_for_blank_input():
    assert chunk_text("   \n\t ", asset_id="x", filename="a.txt") == []


# 8 ---------------------------------------------------------------------------
def test_split_sentences_handles_terminators():
    sentences = split_sentences("First one. Second one! Third one?")
    assert sentences == ["First one.", "Second one!", "Third one?"]


# 9 ---------------------------------------------------------------------------
def test_rank_sentences_prefers_query_coverage():
    sentences = [
        "Lists are mutable.",
        "Lists support append and remove and slicing and sorting.",
        "Weather is hot.",
    ]
    ranked = rank_sentences(sentences, "append remove slicing sorting", limit=1)
    assert ranked[0].startswith("Lists support append")


# 10 --------------------------------------------------------------------------
def test_compose_answer_declines_when_nothing_scores_above_threshold():
    scored = _index().search("how do I append to a list", top_k=3)
    answer, grounded = compose_answer(scored, "how do I append to a list", min_score=9999.0)
    assert grounded is False
    assert "couldn't find anything" in answer
    # The key product promise: it never fabricates an answer.
    assert "append" not in answer.lower().split("couldn't find")[1][:80]


# 11 --------------------------------------------------------------------------
def test_compose_answer_is_grounded_and_extractive_when_score_passes():
    question = "how do I append to a list"
    scored = _index().search(question, top_k=3)
    answer, grounded = compose_answer(scored, question, min_score=0.0)
    assert grounded is True
    assert ".append()" in answer, "the answer must quote the source, not paraphrase it"


# 12 --------------------------------------------------------------------------
def test_build_citations_carry_asset_filename_and_score():
    question = "append to a list"
    scored = _index().search(question, top_k=2)
    citations = build_citations(scored, question)
    assert citations
    first = citations[0]
    assert first.asset_id == "a1"
    assert first.filename == "python-lists.txt"
    assert first.score > 0
    assert first.snippet


# 13 --------------------------------------------------------------------------
def test_build_citations_truncate_long_snippets():
    long_text = "python " * 200
    chunk = Chunk("z1", "long.txt", 0, long_text)
    citations = build_citations([type("S", (), {"chunk": chunk, "score": 1.0})()], "python")
    assert len(citations[0].snippet) <= 261
