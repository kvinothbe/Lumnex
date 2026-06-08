"""Assembles a ContextWindow for one drafting attempt."""

from __future__ import annotations

import sqlite3
from typing import Iterable

from vizuara import config
from vizuara.context.models import (
    ContextWindow,
    RetrievedChunk,
    ThreadMessage,
)
from vizuara.context.summary import load_summary
from vizuara.intent.router import IntentResult, classify
from vizuara.wiki.chunks import WikiChunk, load_chunks
from vizuara.wiki.retrieve import retrieve

# These intents always get the relevant company-wide and product-specific policy
# chunks force-included, even if BM25 didn't surface them. Pricing also force-
# includes the product's pricing chunk so the author never invents numbers.
SENSITIVE_INTENTS = {"billing", "cancellation", "pricing", "discount"}

WIKI_TOP_K = 5
RECENT_HISTORY_FOR_INTENT = 3
ESTIMATED_CHARS_PER_TOKEN = 4   # ~rough; for accounting display only


def _load_thread(thread_id: str) -> list[ThreadMessage]:
    conn = sqlite3.connect(config.LUMENX_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, role, text, ts, rating FROM messages
        WHERE thread_id = ?
        ORDER BY ts ASC
        """,
        (thread_id,),
    ).fetchall()
    conn.close()
    return [ThreadMessage(**dict(r)) for r in rows]


def _last_customer_message(history: list[ThreadMessage]) -> str | None:
    for m in reversed(history):
        if m.role == "customer":
            return m.text
    return None


def _retrieval_query(message: str, intent: IntentResult) -> str:
    """Compose a retrieval query that pulls in the product hint and intent angle."""
    bits = [message]
    if intent.product_id_hint:
        bits.append(intent.product_id_hint)
    bits.append(intent.intent)
    return " ".join(bits)


def _force_chunk(
    chunks_by_id: dict[str, WikiChunk],
    chunk_id: str,
    have: set[str],
    out: list[RetrievedChunk],
) -> None:
    """Append the chunk as force-included if it exists and isn't already in `out`."""
    if chunk_id in have or chunk_id not in chunks_by_id:
        return
    c = chunks_by_id[chunk_id]
    out.append(
        RetrievedChunk(
            chunk_id=c.chunk_id,
            product_id=c.product_id,
            section=c.section,
            title=c.title,
            text=c.text,
            score=0.0,
            forced=True,
        )
    )
    have.add(c.chunk_id)


def _force_sensitive_chunks(
    intent: IntentResult,
    chunks: list[RetrievedChunk],
    all_chunks: list[WikiChunk],
) -> list[RetrievedChunk]:
    """Ensure policy / pricing / refund chunks are present for sensitive intents."""
    if intent.intent not in SENSITIVE_INTENTS:
        return chunks
    by_id = {c.chunk_id: c for c in all_chunks}
    have = {c.chunk_id for c in chunks}
    out = list(chunks)

    if intent.intent == "billing":
        _force_chunk(by_id, "company:refund_window", have, out)
        _force_chunk(by_id, "company:overview", have, out)
        if intent.product_id_hint:
            _force_chunk(by_id, f"{intent.product_id_hint}:refund", have, out)
    elif intent.intent == "cancellation":
        if intent.product_id_hint:
            _force_chunk(by_id, f"{intent.product_id_hint}:cancellation", have, out)
            _force_chunk(by_id, f"{intent.product_id_hint}:refund", have, out)
        _force_chunk(by_id, "company:refund_window", have, out)
    elif intent.intent == "pricing":
        if intent.product_id_hint:
            _force_chunk(by_id, f"{intent.product_id_hint}:pricing", have, out)
        _force_chunk(by_id, "company:annual_discount", have, out)
    elif intent.intent == "discount":
        _force_chunk(by_id, "company:annual_discount", have, out)
        _force_chunk(by_id, "company:startup_program", have, out)
        _force_chunk(by_id, "company:education_program", have, out)
        _force_chunk(by_id, "company:bundle", have, out)
    return out


def _estimate_tokens(*parts: Iterable[str] | str | None) -> int:
    total_chars = 0
    for p in parts:
        if p is None:
            continue
        if isinstance(p, str):
            total_chars += len(p)
        else:
            for x in p:
                if x:
                    total_chars += len(x)
    return total_chars // ESTIMATED_CHARS_PER_TOKEN


def build(thread_id: str, customer_message: str | None = None) -> ContextWindow:
    """Build a ContextWindow for the customer's latest message (or a provided one)."""
    history = _load_thread(thread_id)
    if customer_message is None:
        customer_message = _last_customer_message(history)
    if not customer_message:
        raise ValueError(f"Thread {thread_id!r} has no customer message to draft a reply for.")

    recent_for_intent = [
        {"role": m.role, "text": m.text}
        for m in history[-RECENT_HISTORY_FOR_INTENT:]
        if m.text != customer_message
    ]
    intent = classify(customer_message, recent_history=recent_for_intent or None)

    query = _retrieval_query(customer_message, intent)
    scored = retrieve(query, k=WIKI_TOP_K)
    wiki_chunks: list[RetrievedChunk] = [
        RetrievedChunk(
            chunk_id=s.chunk.chunk_id,
            product_id=s.chunk.product_id,
            section=s.chunk.section,
            title=s.chunk.title,
            text=s.chunk.text,
            score=round(s.score, 4),
            forced=False,
        )
        for s in scored
    ]

    all_chunks = load_chunks()
    wiki_chunks = _force_sensitive_chunks(intent, wiki_chunks, all_chunks)

    corpus_summary = load_summary()

    ctx = ContextWindow(
        thread_id=thread_id,
        customer_message=customer_message,
        intent=intent,
        product_id_hint=intent.product_id_hint,
        thread_history=history,
        corpus_summary=corpus_summary,
        wiki_chunks=wiki_chunks,
        similar_qa=[],          # populated in Phase 5 once feedback log exists
        retrieval_query=query,
        sensitive=intent.intent in SENSITIVE_INTENTS,
    )
    ctx.estimated_input_tokens = _estimate_tokens(
        ctx.customer_message,
        ctx.corpus_summary,
        (m.text for m in ctx.thread_history),
        (c.text for c in ctx.wiki_chunks),
    )
    return ctx


def build_for_message(
    customer_message: str,
    thread_id: str = "synthetic",
    history: list[ThreadMessage] | None = None,
) -> ContextWindow:
    """Build a ContextWindow for a standalone message (no real thread in SQLite).

    Useful for smoke tests, batch evaluation, and shadow-mode where the source
    of the message is not the live LumenX inbox. Mirrors build() but skips the
    DB load and lets you optionally inject thread history.
    """
    history = history or []
    recent_for_intent = [
        {"role": m.role, "text": m.text}
        for m in history[-RECENT_HISTORY_FOR_INTENT:]
        if m.text != customer_message
    ]
    intent = classify(customer_message, recent_history=recent_for_intent or None)

    query = _retrieval_query(customer_message, intent)
    scored = retrieve(query, k=WIKI_TOP_K)
    wiki_chunks: list[RetrievedChunk] = [
        RetrievedChunk(
            chunk_id=s.chunk.chunk_id,
            product_id=s.chunk.product_id,
            section=s.chunk.section,
            title=s.chunk.title,
            text=s.chunk.text,
            score=round(s.score, 4),
            forced=False,
        )
        for s in scored
    ]
    all_chunks = load_chunks()
    wiki_chunks = _force_sensitive_chunks(intent, wiki_chunks, all_chunks)

    ctx = ContextWindow(
        thread_id=thread_id,
        customer_message=customer_message,
        intent=intent,
        product_id_hint=intent.product_id_hint,
        thread_history=history,
        corpus_summary=load_summary(),
        wiki_chunks=wiki_chunks,
        similar_qa=[],
        retrieval_query=query,
        sensitive=intent.intent in SENSITIVE_INTENTS,
    )
    ctx.estimated_input_tokens = _estimate_tokens(
        ctx.customer_message,
        ctx.corpus_summary,
        (m.text for m in ctx.thread_history),
        (c.text for c in ctx.wiki_chunks),
    )
    return ctx


def _cli() -> None:
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m vizuara.context.builder <thread_id> [override_message]")
        sys.exit(2)
    tid = sys.argv[1]
    override = sys.argv[2] if len(sys.argv) > 2 else None
    ctx = build(tid, override)
    print(json.dumps(ctx.model_dump(), indent=2, default=str))


if __name__ == "__main__":
    _cli()
