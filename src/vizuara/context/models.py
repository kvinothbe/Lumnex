"""ContextWindow and its component models.

Every field carries enough provenance that the Phase 8 dashboard can render
exactly what went to the LLM and where each piece came from.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from vizuara.intent.router import IntentResult


class ThreadMessage(BaseModel):
    id: str
    role: str            # "customer" | "admin"
    text: str
    ts: str
    rating: int | None = None


class RetrievedChunk(BaseModel):
    """A wiki chunk pulled in by retrieval or force-included for a sensitive intent."""

    chunk_id: str
    product_id: str | None
    section: str
    title: str
    text: str
    score: float        # BM25 score, or 0.0 if force-included
    forced: bool = False  # True if added because the intent is sensitive, not by retrieval


class QAPair(BaseModel):
    """Similar past Q&A pair from the feedback log. Empty until Phase 5 starts logging."""

    draft_id: str
    customer_message: str
    final_text: str
    intent: str
    was_accepted_as_is: bool


class ContextWindow(BaseModel):
    """Everything the LLM author will see for one drafting attempt.

    The dashboard will render this verbatim, so any change here flows through to
    user-visible provenance.
    """

    thread_id: str
    customer_message: str
    intent: IntentResult
    product_id_hint: str | None = None

    thread_history: list[ThreadMessage] = Field(default_factory=list)
    corpus_summary: str = ""        # one-time-summarized house-style doc
    wiki_chunks: list[RetrievedChunk] = Field(default_factory=list)
    similar_qa: list[QAPair] = Field(default_factory=list)

    # Tracing fields — useful for the dashboard / debugging:
    estimated_input_tokens: int = 0
    retrieval_query: str = ""
    sensitive: bool = False         # true for billing / cancellation; triggers extra policy chunks
