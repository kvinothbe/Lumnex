"""Sonnet judge: given (customer_message, gold_reply, ai_draft), return similarity 0-1.

Used to build proxy confidence labels for Source A bootstrap samples (re-drafted
seeded threads). The score reflects how close the AI draft is to the actual
admin reply that worked — high = the human would likely accept the draft as-is.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from vizuara.cost import get_tracker
from vizuara.cost.pricing import SONNET


SYSTEM_PROMPT = """You evaluate how close an AI-drafted customer-support reply is to a
known-good admin reply ("gold") for the same customer message.

Output a similarity score 0.0 to 1.0 and a one-sentence reason.

CALIBRATION:
- 1.0  identical content and tone; the human would send the AI draft as-is.
- 0.8  same factual content, slightly different phrasing, same tone.
- 0.6  most key points covered, minor omissions or tone drift.
- 0.4  partial answer, missing important facts the gold included.
- 0.2  largely wrong content or major tone mismatch.
- 0.0  wrong facts, contradicts the gold, or fails to address the question.

If the AI draft hallucinates a number (wrong price, wrong day count, wrong
percent), the score must be 0.2 or below — this is the most important signal
for the downstream confidence net.

If the AI draft correctly ABSTAINS ("I don't have that information") AND the
gold reply also contained genuinely missing info, score 0.7. If the gold reply
provided the info but the AI abstained, score 0.3.

Always call the emit_judgement tool. Do not produce free-form text."""


_TOOL = {
    "name": "emit_judgement",
    "description": "Emit similarity score and one-sentence reason.",
    "input_schema": {
        "type": "object",
        "properties": {
            "similarity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string"},
        },
        "required": ["similarity", "reason"],
    },
}


class JudgeResult(BaseModel):
    similarity: float = Field(ge=0.0, le=1.0)
    reason: str


def judge(customer_message: str, gold_reply: str, ai_draft: str) -> JudgeResult:
    tracker = get_tracker()
    user = (
        f"Customer message:\n{customer_message}\n\n"
        f"Gold admin reply:\n{gold_reply}\n\n"
        f"AI draft:\n{ai_draft}"
    )
    resp = tracker.messages_create(
        stage="judge",
        model=SONNET,
        max_tokens=256,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "emit_judgement"},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_judgement":
            data = dict(block.input)
            return JudgeResult(similarity=float(data["similarity"]), reason=data.get("reason", ""))
    raise RuntimeError(f"judge returned no tool_use block: {resp.content!r}")
