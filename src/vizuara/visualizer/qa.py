"""Wiki QA: retrieve top-k chunks (BM25) then ask Sonnet, with hard citation rules."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from vizuara.cost import get_tracker
from vizuara.cost.pricing import SONNET
from vizuara.wiki.retrieve import retrieve


class Citation(BaseModel):
    chunk_id: str
    title: str
    snippet: str


class AnswerResult(BaseModel):
    answer: str
    cited_chunk_ids: list[str] = Field(default_factory=list)
    abstained: bool = False
    retrieved: list[Citation] = Field(default_factory=list)


SYSTEM_PROMPT = """You answer questions about Lumenx using ONLY the wiki excerpts provided.

HARD RULES:
1. If the excerpts do not contain the answer, set abstained=true and say
   "I don't have that information in the wiki." Never invent facts.
2. Never invent pricing, refund windows, free-trial durations, plan tiers,
   discount percentages, integrations, or SLAs.
3. Every factual claim you make must be supported by at least one chunk you cite.
4. cited_chunk_ids must contain ONLY chunk_ids that appear in the provided excerpts.
5. Keep the answer concise (2-5 sentences). Use plain text, no markdown headers.
6. If two products are involved, cite chunks from both.

Always call the emit_answer tool. Do not produce any free-form text outside the tool."""


def _excerpt_block(retrieved) -> str:
    parts = []
    for sc in retrieved:
        c = sc.chunk
        parts.append(
            f"[chunk_id: {c.chunk_id}]\n"
            f"title: {c.title}\n"
            f"---\n{c.text.strip()}\n"
        )
    return "\n\n".join(parts)


def _tool() -> dict[str, Any]:
    return {
        "name": "emit_answer",
        "description": "Emit the final answer with citations to the wiki chunks used.",
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "cited_chunk_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "chunk_ids from the provided excerpts that support the answer.",
                },
                "abstained": {"type": "boolean"},
            },
            "required": ["answer", "cited_chunk_ids", "abstained"],
        },
    }


def answer(question: str, k: int = 6) -> AnswerResult:
    retrieved = retrieve(question, k=k)
    if not retrieved:
        return AnswerResult(
            answer="I don't have that information in the wiki.",
            cited_chunk_ids=[],
            abstained=True,
            retrieved=[],
        )

    excerpts = _excerpt_block(retrieved)
    user = (
        f"Question:\n{question}\n\n"
        f"Wiki excerpts (use only these):\n\n{excerpts}"
    )

    tracker = get_tracker()
    resp = tracker.messages_create(
        stage="wiki_qa",
        model=SONNET,
        max_tokens=512,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=[_tool()],
        tool_choice={"type": "tool", "name": "emit_answer"},
        messages=[{"role": "user", "content": user}],
    )

    payload = None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_answer":
            payload = dict(block.input)
            break
    if payload is None:
        raise RuntimeError(f"emit_answer not returned. content={resp.content!r}")

    citations = [
        Citation(
            chunk_id=sc.chunk.chunk_id,
            title=sc.chunk.title,
            snippet=sc.chunk.text.strip(),
        )
        for sc in retrieved
    ]
    return AnswerResult(
        answer=payload["answer"],
        cited_chunk_ids=payload.get("cited_chunk_ids") or [],
        abstained=bool(payload.get("abstained", False)),
        retrieved=citations,
    )
