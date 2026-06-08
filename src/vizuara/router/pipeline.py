"""End-to-end pipeline glue: customer message in, draft logged out (and maybe sent).

Intentionally one function so the polling daemon (Phase 9) and the dashboard
queue worker (Phase 8) call the same code path. dry_run=True keeps the LumenX
POST disabled even when auto-send is otherwise enabled — used in tests, shadow
mode, and any first-time run on a new threshold.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from vizuara import config
from vizuara.author import draft
from vizuara.author.drafter import DraftResult
from vizuara.confidence.features import extract_features, view_of
from vizuara.confidence.mlp import ConfidenceMLP, infer, load
from vizuara.context import build, build_for_message
from vizuara.context.models import ContextWindow
from vizuara.feedback import log_draft, log_send
from vizuara.lumenx.client import LumenXClient
from vizuara.router.decision import Decision, RouteResult, decide


CONFIDENCE_CHECKPOINT = config.DATA_DIR / "confidence.pt"


@lru_cache(maxsize=1)
def _model() -> ConfidenceMLP:
    if not CONFIDENCE_CHECKPOINT.exists():
        raise RuntimeError(
            f"Confidence checkpoint missing at {CONFIDENCE_CHECKPOINT}. "
            "Run `python -m vizuara.confidence.train` first."
        )
    model, _meta = load(CONFIDENCE_CHECKPOINT)
    return model


class PipelineResult(BaseModel):
    draft_id: str
    draft: DraftResult
    route: RouteResult
    confidence: float
    auto_sent: bool
    reply_message_id: str | None = None
    context_token_estimate: int = 0


def process_message(
    *,
    thread_id: str,
    customer_message: str | None = None,
    customer_message_id: str | None = None,
    dry_run: bool = True,
    threshold: float | None = None,
    auto_send_enabled: bool | None = None,
    use_synthetic_context: bool = False,
) -> PipelineResult:
    """Run the full intent → context → draft → confidence → decide → log pipeline.

    Args:
        thread_id: LumenX thread id, or a synthetic id if use_synthetic_context=True.
        customer_message: override the last customer message in the thread.
            Required when use_synthetic_context=True.
        customer_message_id: LumenX message id of the customer message we're replying to.
        dry_run: if True, never POST to LumenX even on AUTO decisions.
        threshold / auto_send_enabled: override env defaults for one call (shadow mode, A/B).
        use_synthetic_context: build a ContextWindow without loading the real thread.
    """
    if use_synthetic_context:
        if not customer_message:
            raise ValueError("customer_message is required when use_synthetic_context=True")
        ctx: ContextWindow = build_for_message(customer_message, thread_id=thread_id)
    else:
        ctx = build(thread_id, customer_message)

    drafted = draft(ctx)

    feats = extract_features(ctx, view_of(drafted))
    confidence = infer(_model(), feats)

    thr = config.AUTO_SEND_THRESHOLD if threshold is None else threshold
    enabled = config.AUTO_SEND_ENABLED if auto_send_enabled is None else auto_send_enabled
    route = decide(
        intent=ctx.intent.intent,
        abstained=drafted.abstained,
        confidence=confidence,
        threshold=thr,
        auto_send_enabled=enabled,
    )

    draft_id = log_draft(ctx, drafted, customer_message_id=customer_message_id)

    sent_id: str | None = None
    auto_sent = False
    if route.decision == Decision.AUTO and not dry_run:
        with LumenXClient() as client:
            resp = client.reply(
                thread_id,
                drafted.draft_text,
                draft_source="agent",
                confidence=confidence,
            )
        # LumenX returns the message it created — try common id fields.
        sent_id = resp.get("id") or resp.get("message_id") or resp.get("messageId")
        log_send(draft_id, mode="auto", reply_message_id=sent_id, confidence=confidence)
        auto_sent = True

    return PipelineResult(
        draft_id=draft_id,
        draft=drafted,
        route=route,
        confidence=confidence,
        auto_sent=auto_sent,
        reply_message_id=sent_id,
        context_token_estimate=ctx.estimated_input_tokens,
    )
