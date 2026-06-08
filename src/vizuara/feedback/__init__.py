"""Feedback log: drafts, human edits, customer ratings, and send events.

This is the durable record of every interaction. It is both:
- the training set the Phase 6 confidence MLP learns from, and
- the corpus the context builder will retrieve similar past Q&A pairs from.
"""

from vizuara.feedback.store import (
    get_full_record,
    list_drafts,
    log_draft,
    log_edit,
    log_rating,
    log_send,
    open_db,
)

__all__ = [
    "get_full_record",
    "list_drafts",
    "log_draft",
    "log_edit",
    "log_rating",
    "log_send",
    "open_db",
]
