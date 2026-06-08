"""Haiku intent classifier with tool-use forced output."""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from vizuara import config
from vizuara.cost import get_tracker
from vizuara.cost.pricing import HAIKU

# Exactly the 10 labels the LumenX platform uses on its 100 seeded threads, plus
# a fallback. The first 10 strings MUST stay in sync with the platform vocabulary
# so the evaluator can compare directly.
INTENTS: list[str] = [
    "pricing",
    "feature",
    "technical",
    "integration",
    "discount",
    "cancellation",
    "billing",
    "multi-product",
    "greeting",
    "compare-competitor",
    "other",
]


class IntentResult(BaseModel):
    intent: str
    sub_intent: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    product_id_hint: str | None = None


@lru_cache(maxsize=1)
def _product_ids() -> list[str]:
    conn = sqlite3.connect(config.LUMENX_DB_PATH)
    rows = conn.execute("SELECT id FROM products ORDER BY id").fetchall()
    conn.close()
    return [r[0] for r in rows]


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    pids = ", ".join(_product_ids())
    return f"""You are an intent classifier for Lumenx customer-support messages.

Lumenx is a multi-product SaaS company. Available product ids: {pids}.

Classify the customer's latest message into EXACTLY ONE of these intents:

- pricing           — asks the COST of a plan or product ("how much", "what's the price",
                      "what does Pro cost"). Pure cost question, no currency / invoice angle.
- feature           — what the product does, capabilities, how X works inside the product
- technical         — bug, error, broken, not working, troubleshooting, can't log in
- integration       — does it work with Slack/Gmail/Notion/etc, does it support X
- discount          — wants a discount, promo code, startup program, education program,
                      ANNUAL-vs-MONTHLY question, BUNDLE / multi-product-savings question
- cancellation      — cancel my subscription, how do I cancel, what happens after cancel.
                      A message that says BOTH "cancel" AND "refund" is cancellation.
- billing           — invoice questions, payment problems, charge questions, BILLING CURRENCY
                      ("do you bill in EUR?", "all prices in dollars?"), invoice downloads,
                      standalone refund requests (no cancellation mentioned)
- multi-product     — the question involves TWO OR MORE Lumenx products by name
- greeting          — only "hi" / "hello" / "good morning" with NO real product question
- compare-competitor — compares Lumenx to a third-party tool (Notion, Asana, ChatGPT, ...)
- other             — does not fit any of the above

CRITICAL RULES:
1. "hi, how much does it cost?" is NOT greeting — it is pricing. Greeting is ONLY for messages
   with no actual product question. Any follow-up question wins over the hello.
2. The word "pricing" or "price" appearing in the message does NOT automatically mean pricing.
   If the actual question is about currency ("do you bill in EUR"), invoice format, refunds,
   or annual vs monthly billing, classify by the question, not by the trigger word.
3. "Cancel + refund" in the same message → cancellation. "Refund" alone → billing.
4. "Annual vs monthly", "annual cheaper", "yearly discount" → discount (not pricing).
5. "Bundle", "discount for buying multiple" → discount (even if a count of products is given).
6. confidence is your own self-estimated probability that this label is correct.
7. product_id_hint should be a product id from the list above, or omitted if no specific product.

Examples (always match the platform's labelling):
- "How much does PollWise cost?"              → pricing
- "Is annual cheaper than monthly for X?"     → discount
- "Cancel pls. Will I get a refund?"          → cancellation
- "Do you bill in EUR?"                       → billing
- "Where do I download invoices?"             → billing
- "Are all prices in dollars?"                → billing
- "I want 5 of your products, anything bundled?" → discount
- "hi"                                        → greeting
- "hi how much does Pro cost?"                → pricing

Call the emit_intent tool with your decision. Do not write any natural-language reply."""


def _tool_def() -> dict[str, Any]:
    return {
        "name": "emit_intent",
        "description": "Emit the classified intent for the customer's latest message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": INTENTS},
                "sub_intent": {
                    "type": "string",
                    "description": "Short free-text refinement, e.g. 'asks for annual price'. Optional.",
                },
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "product_id_hint": {
                    "type": "string",
                    "enum": _product_ids(),
                    "description": "The product id the question is about, if any.",
                },
            },
            "required": ["intent", "confidence"],
        },
    }


def _build_user_content(message: str, recent_history: list[dict] | None) -> str:
    if not recent_history:
        return f"Customer message:\n{message}"
    history = "\n".join(f"{m['role']}: {m['text']}" for m in recent_history)
    return (
        f"Recent thread history:\n{history}\n\nNew customer message:\n{message}"
    )


def classify(message: str, recent_history: list[dict] | None = None) -> IntentResult:
    """Classify a customer message via Haiku. Forces a tool_use response."""
    tracker = get_tracker()
    resp = tracker.messages_create(
        stage="intent",
        model=HAIKU,
        max_tokens=256,
        system=[
            {
                "type": "text",
                "text": _system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[_tool_def()],
        tool_choice={"type": "tool", "name": "emit_intent"},
        messages=[{"role": "user", "content": _build_user_content(message, recent_history)}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_intent":
            data = dict(block.input)
            # Normalize empty-string optionals so pydantic doesn't see "" as a value.
            for k in ("sub_intent", "product_id_hint"):
                if data.get(k) == "":
                    data[k] = None
            return IntentResult(**data)
    raise RuntimeError(
        f"Intent classifier returned no emit_intent tool_use block. Content: {resp.content!r}"
    )
