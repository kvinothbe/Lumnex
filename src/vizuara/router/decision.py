"""Pure-function router: takes intent + abstained + confidence, returns a decision.

No I/O, no LLM calls — easy to unit-test. The pipeline glue in `pipeline.py`
handles everything else (drafting, logging, optional send).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


# Intents where the cost of a wrong reply is highest. PLAN.md and the
# anti-hallucination memory both pin these as ALWAYS-human-review, regardless
# of how confident the MLP is. Pricing and discount are NOT in this set — they
# can auto-send if the draft cites the wiki and the MLP scores high enough.
SENSITIVE_INTENTS: set[str] = {"billing", "cancellation"}


class Decision(str, Enum):
    AUTO = "auto"
    REVIEW = "review"


class Reason(str, Enum):
    AUTO_SEND_DISABLED = "auto_send_disabled"   # master switch is off
    SENSITIVE_INTENT   = "sensitive_intent"     # billing / cancellation always human
    ABSTAINED          = "abstained"            # author refused to answer
    LOW_CONFIDENCE     = "low_confidence"       # MLP score below threshold
    HIGH_CONFIDENCE    = "high_confidence"      # passed every gate


class RouteResult(BaseModel):
    decision: Decision
    reason: Reason
    confidence: float
    threshold: float
    auto_send_enabled: bool


def decide(
    *,
    intent: str,
    abstained: bool,
    confidence: float,
    threshold: float,
    auto_send_enabled: bool,
) -> RouteResult:
    """Decide whether the draft can auto-send or must go to human review."""
    if not auto_send_enabled:
        return RouteResult(
            decision=Decision.REVIEW, reason=Reason.AUTO_SEND_DISABLED,
            confidence=confidence, threshold=threshold,
            auto_send_enabled=False,
        )
    if intent in SENSITIVE_INTENTS:
        return RouteResult(
            decision=Decision.REVIEW, reason=Reason.SENSITIVE_INTENT,
            confidence=confidence, threshold=threshold,
            auto_send_enabled=True,
        )
    if abstained:
        return RouteResult(
            decision=Decision.REVIEW, reason=Reason.ABSTAINED,
            confidence=confidence, threshold=threshold,
            auto_send_enabled=True,
        )
    if confidence < threshold:
        return RouteResult(
            decision=Decision.REVIEW, reason=Reason.LOW_CONFIDENCE,
            confidence=confidence, threshold=threshold,
            auto_send_enabled=True,
        )
    return RouteResult(
        decision=Decision.AUTO, reason=Reason.HIGH_CONFIDENCE,
        confidence=confidence, threshold=threshold,
        auto_send_enabled=True,
    )
