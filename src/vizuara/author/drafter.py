"""draft(context_window) — Sonnet call that produces the suggested admin reply."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from vizuara.author.prompts import system_prompt, tool_def
from vizuara.context.models import ContextWindow
from vizuara.cost import get_tracker
from vizuara.cost.pricing import SONNET, cost_usd


class Citation(BaseModel):
    chunk_id: str
    product_id: str | None = None
    section: str | None = None


class DraftResult(BaseModel):
    draft_text: str
    citations: list[Citation] = Field(default_factory=list)
    abstained: bool = False
    model: str = SONNET
    cited_chunk_ids: list[str] = Field(default_factory=list)

    # Token + cost accounting for the single API call that produced this draft.
    # Captured directly from the Anthropic response so the feedback log doesn't
    # have to read the JSONL cost log back to find this draft's row.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0


def _format_chunks(ctx: ContextWindow) -> str:
    if not ctx.wiki_chunks:
        return "(no wiki excerpts retrieved)"
    parts: list[str] = []
    for c in ctx.wiki_chunks:
        forced_tag = "  [forced]" if c.forced else ""
        parts.append(
            f"[chunk_id: {c.chunk_id}]{forced_tag}\n"
            f"title: {c.title}\n"
            f"---\n{c.text.strip()}\n"
        )
    return "\n\n".join(parts)


def _format_history(ctx: ContextWindow) -> str:
    if not ctx.thread_history:
        return "(no prior messages)"
    lines = []
    for m in ctx.thread_history:
        lines.append(f"{m.role}: {m.text}")
    return "\n".join(lines)


def _build_user_content(ctx: ContextWindow) -> str:
    return (
        f"INTENT: {ctx.intent.intent}"
        f"  (product hint: {ctx.product_id_hint or 'none'};"
        f" classifier confidence: {ctx.intent.confidence:.2f};"
        f" sensitive: {ctx.sensitive})\n\n"
        f"WIKI EXCERPTS (use ONLY these for facts):\n\n{_format_chunks(ctx)}\n\n"
        f"THREAD HISTORY:\n{_format_history(ctx)}\n\n"
        f"NEW CUSTOMER MESSAGE:\n{ctx.customer_message}"
    )


def _chunks_index(ctx: ContextWindow) -> dict[str, tuple[str | None, str]]:
    return {c.chunk_id: (c.product_id, c.section) for c in ctx.wiki_chunks}


def draft(ctx: ContextWindow) -> DraftResult:
    """Ask Sonnet to draft a reply for the given ContextWindow."""
    tracker = get_tracker()
    sys = system_prompt(ctx.corpus_summary)

    resp = tracker.messages_create(
        stage="author",
        model=SONNET,
        max_tokens=600,
        system=[
            {
                "type": "text",
                "text": sys,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[tool_def()],
        tool_choice={"type": "tool", "name": "emit_draft"},
        messages=[{"role": "user", "content": _build_user_content(ctx)}],
    )

    payload: dict[str, Any] | None = None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_draft":
            payload = dict(block.input)
            break
    if payload is None:
        raise RuntimeError(f"emit_draft tool_use missing in author response: {resp.content!r}")

    cited_ids = list(payload.get("citations") or [])
    idx = _chunks_index(ctx)
    citations = [
        Citation(chunk_id=cid, product_id=idx.get(cid, (None, None))[0], section=idx.get(cid, (None, None))[1])
        for cid in cited_ids
    ]

    usage = getattr(resp, "usage", None)
    in_tok = int(getattr(usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(usage, "output_tokens", 0) or 0)
    cache_r = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_w = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    usd = cost_usd(
        model=SONNET,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cache_r,
        cache_creation_tokens=cache_w,
    )

    return DraftResult(
        draft_text=payload["draft_text"].strip(),
        citations=citations,
        abstained=bool(payload.get("abstained", False)),
        model=SONNET,
        cited_chunk_ids=cited_ids,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cache_r,
        cache_creation_tokens=cache_w,
        cost_usd=usd,
    )
