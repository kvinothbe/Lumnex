"""Hard-coded short-circuit replies. Used so trivial messages don't trigger a drafting pass."""

from __future__ import annotations

GREETING_REPLY = (
    "Hi! Thanks for reaching out. What can I help you with today — pricing, features, "
    "or something else?"
)


def short_circuit_reply(intent: str) -> str | None:
    """Return a canned reply for trivial intents, or None to fall through to drafting.

    Only greetings short-circuit. "other" intents still go to the author so the
    drafter can produce a generic-but-context-aware reply (or abstain), rather than
    a blanket form response.
    """
    if intent == "greeting":
        return GREETING_REPLY
    return None
