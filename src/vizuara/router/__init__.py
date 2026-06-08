"""Router: decides whether a draft is auto-sent or sent to the human review queue."""

from vizuara.router.decision import (
    SENSITIVE_INTENTS,
    Decision,
    Reason,
    RouteResult,
    decide,
)
from vizuara.router.pipeline import PipelineResult, process_message

__all__ = [
    "SENSITIVE_INTENTS",
    "Decision",
    "Reason",
    "RouteResult",
    "decide",
    "PipelineResult",
    "process_message",
]
