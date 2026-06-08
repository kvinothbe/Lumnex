"""Feature extractor for the confidence MLP.

Pure-Python, deterministic, no extra LLM calls (sentiment is a keyword proxy).
Same function is used at training time (bootstrap samples) and at inference time
(live drafts) so train/serve features stay in lock-step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from vizuara.context.models import ContextWindow
from vizuara.intent.router import INTENTS

# --- normalization constants (kept here so they're traceable in checkpoints) ---
_NORM_RETRIEVAL_SCORE = 20.0
_NORM_FORCED_CHUNKS   = 5.0
_NORM_DRAFT_CHARS     = 600.0
_NORM_SENTENCES       = 6.0
_NORM_DOLLAR          = 5.0
_NORM_PERCENT         = 3.0
_NORM_DAY_MENTIONS    = 3.0
_NORM_CITATIONS       = 5.0
_NORM_WIKI_CHUNKS     = 8.0
_NORM_THREAD_DEPTH    = 10.0
_NORM_CUSTOMER_TURNS  = 5.0
_NORM_URGENCY         = 3.0

# --- keyword lists for the sentiment / urgency proxies ---
_POSITIVE = {"thanks", "thank", "great", "awesome", "love", "perfect", "helpful", "appreciate"}
_NEGATIVE = {"angry", "frustrated", "broken", "useless", "terrible", "awful", "worst", "disappointed", "bug", "error"}
_URGENT   = {"now", "urgent", "asap", "immediately", "right away", "today"}

_DOLLAR_RE   = re.compile(r"\$\s?\d")
_PERCENT_RE  = re.compile(r"\d+\s?%")
_DAYS_RE     = re.compile(r"\b\d+\s*-?\s*day(?:s)?\b", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"[.!?]+")
_WORD_RE     = re.compile(r"[a-z']+", re.IGNORECASE)


@dataclass
class _DraftView:
    """Subset of DraftResult fields needed for feature extraction.

    Decoupled from the actual DraftResult class so we can also feed in
    mutated / synthetic drafts that don't come from the author.
    """

    draft_text: str
    abstained: bool
    cited_chunk_ids: list[str]


# Build feature names in the exact order they go into the vector. This list IS the schema.
FEATURE_NAMES: list[str] = (
    [f"intent_is_{i}" for i in INTENTS]      # 11
    + [
        "retrieval_top1_score",              # 12
        "retrieval_top3_mean",               # 13
        "forced_chunks_count",               # 14
        "abstained",                         # 15
        "draft_chars",                       # 16
        "sentence_count",                    # 17
        "dollar_count",                      # 18
        "percent_count",                     # 19
        "day_mention_count",                 # 20
        "citations_count",                   # 21
        "wiki_chunks_count",                 # 22
        "product_hint_present",              # 23
        "thread_depth",                      # 24
        "customer_turns",                    # 25
        "sentiment_proxy",                   # 26
        "urgency_count",                     # 27
        "hallucination_risk",                # 28
    ]
)
FEATURE_DIM: int = len(FEATURE_NAMES)


def _sentiment(text: str) -> float:
    words = {w.lower() for w in _WORD_RE.findall(text)}
    pos = len(words & _POSITIVE)
    neg = len(words & _NEGATIVE)
    if pos == 0 and neg == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / max(1, pos + neg)))


def _urgency(text: str) -> int:
    t = text.lower()
    return sum(1 for kw in _URGENT if kw in t)


def _hallucination_risk(draft_text: str, citations: list[str], wiki_chunk_ids: list[str]) -> float:
    """1.0 if the draft cites a number-shaped fact (price/days/percent) but no
    supporting chunk is referenced; else 0.0.

    This is the single most important feature for the auto-send gate: a draft
    that quotes a dollar amount without citing the pricing chunk is exactly
    the kind of hallucinated reply we want to BLOCK.
    """
    mentions_money = bool(_DOLLAR_RE.search(draft_text))
    mentions_pct   = bool(_PERCENT_RE.search(draft_text))
    mentions_days  = bool(_DAYS_RE.search(draft_text))
    if not (mentions_money or mentions_pct or mentions_days):
        return 0.0
    # Heuristic: citing ANY chunk that is itself in the bundle counts as supported.
    cited_in_bundle = set(citations) & set(wiki_chunk_ids)
    return 0.0 if cited_in_bundle else 1.0


def extract_features(ctx: ContextWindow, draft: _DraftView) -> list[float]:
    """Return a FEATURE_DIM-length list of floats."""
    f: list[float] = []

    # 1-11: one-hot intent
    for label in INTENTS:
        f.append(1.0 if ctx.intent.intent == label else 0.0)

    # 12: top-1 BM25 score
    scores = [c.score for c in ctx.wiki_chunks if not c.forced]
    top1 = scores[0] if scores else 0.0
    f.append(min(1.0, top1 / _NORM_RETRIEVAL_SCORE))

    # 13: top-3 mean BM25 score
    top3 = sum(scores[:3]) / max(1, min(3, len(scores))) if scores else 0.0
    f.append(min(1.0, top3 / _NORM_RETRIEVAL_SCORE))

    # 14: count of force-included chunks
    f.append(min(1.0, sum(1 for c in ctx.wiki_chunks if c.forced) / _NORM_FORCED_CHUNKS))

    # 15: abstained
    f.append(1.0 if draft.abstained else 0.0)

    # 16: draft chars
    f.append(min(1.0, len(draft.draft_text) / _NORM_DRAFT_CHARS))

    # 17: sentence count
    sentences = sum(1 for s in _SENTENCE_RE.split(draft.draft_text) if s.strip())
    f.append(min(1.0, sentences / _NORM_SENTENCES))

    # 18: dollar count
    f.append(min(1.0, len(_DOLLAR_RE.findall(draft.draft_text)) / _NORM_DOLLAR))

    # 19: percent count
    f.append(min(1.0, len(_PERCENT_RE.findall(draft.draft_text)) / _NORM_PERCENT))

    # 20: day mention count
    f.append(min(1.0, len(_DAYS_RE.findall(draft.draft_text)) / _NORM_DAY_MENTIONS))

    # 21: citations count
    f.append(min(1.0, len(draft.cited_chunk_ids) / _NORM_CITATIONS))

    # 22: wiki chunks count in bundle
    f.append(min(1.0, len(ctx.wiki_chunks) / _NORM_WIKI_CHUNKS))

    # 23: product hint present
    f.append(1.0 if ctx.product_id_hint else 0.0)

    # 24: thread depth
    f.append(min(1.0, len(ctx.thread_history) / _NORM_THREAD_DEPTH))

    # 25: customer turns
    cust_turns = sum(1 for m in ctx.thread_history if m.role == "customer")
    f.append(min(1.0, cust_turns / _NORM_CUSTOMER_TURNS))

    # 26: sentiment proxy on the customer message
    f.append(_sentiment(ctx.customer_message))

    # 27: urgency count on the customer message
    f.append(min(1.0, _urgency(ctx.customer_message) / _NORM_URGENCY))

    # 28: hallucination risk
    wiki_chunk_ids = [c.chunk_id for c in ctx.wiki_chunks]
    f.append(_hallucination_risk(draft.draft_text, draft.cited_chunk_ids, wiki_chunk_ids))

    assert len(f) == FEATURE_DIM, f"feature count mismatch: {len(f)} != {FEATURE_DIM}"
    return f


def view_of(draft) -> _DraftView:
    """Adapter: build a _DraftView from a DraftResult (or anything with the same fields)."""
    return _DraftView(
        draft_text=draft.draft_text,
        abstained=bool(getattr(draft, "abstained", False)),
        cited_chunk_ids=list(getattr(draft, "cited_chunk_ids", []) or []),
    )
