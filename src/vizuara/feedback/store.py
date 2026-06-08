"""Write + read API for the feedback log."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vizuara import config
from vizuara.author.drafter import DraftResult
from vizuara.context.models import ContextWindow
from vizuara.feedback.schema import ensure_schema, open_db as _open

FEEDBACK_DB_PATH = config.DATA_DIR / "feedback.db"


def open_db(path: Path | None = None) -> sqlite3.Connection:
    conn = _open(path or FEEDBACK_DB_PATH)
    ensure_schema(conn)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- edit distance ----------

def levenshtein(a: str, b: str) -> int:
    """Char-level Levenshtein distance. O(len(a)*len(b)) time, O(min) space."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def edit_ratio(a: str, b: str) -> float:
    """1.0 = identical, 0.0 = totally different. Defined as 1 - dist/max_len."""
    if a == b:
        return 1.0
    m = max(len(a), len(b))
    if m == 0:
        return 1.0
    return max(0.0, 1.0 - levenshtein(a, b) / m)


# ---------- writers ----------

def log_draft(
    ctx: ContextWindow,
    result: DraftResult,
    *,
    customer_message_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Insert a row in drafts. Returns the new draft_id."""
    draft_id = uuid.uuid4().hex
    own = conn is None
    conn = conn or open_db()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO drafts (
                    id, thread_id, customer_message_id, intent, product_id_hint,
                    draft_text, abstained, citations_json, context_window_json,
                    model, input_tokens, cache_read_tokens, cache_creation_tokens,
                    output_tokens, cost_usd, ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    ctx.thread_id,
                    customer_message_id,
                    ctx.intent.intent,
                    ctx.product_id_hint,
                    result.draft_text,
                    1 if result.abstained else 0,
                    json.dumps(result.cited_chunk_ids),
                    ctx.model_dump_json(),
                    result.model,
                    result.input_tokens,
                    result.cache_read_tokens,
                    result.cache_creation_tokens,
                    result.output_tokens,
                    result.cost_usd,
                    _now(),
                ),
            )
    finally:
        if own:
            conn.close()
    return draft_id


def log_edit(
    draft_id: str,
    final_text: str,
    *,
    human_reviewer: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Insert into edits. Computes edit_distance / edit_ratio vs the original draft."""
    own = conn is None
    conn = conn or open_db()
    try:
        row = conn.execute("SELECT draft_text FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if not row:
            raise ValueError(f"No draft with id {draft_id!r}")
        original = row["draft_text"]
        dist = levenshtein(original, final_text)
        ratio = edit_ratio(original, final_text)
        with conn:
            cur = conn.execute(
                """
                INSERT INTO edits (draft_id, final_text, edit_distance, edit_ratio,
                                   was_accepted_as_is, human_reviewer, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (draft_id, final_text, dist, ratio, 1 if dist == 0 else 0, human_reviewer, _now()),
            )
            return cur.lastrowid
    finally:
        if own:
            conn.close()


def log_rating(
    draft_id: str,
    customer_rating: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    own = conn is None
    conn = conn or open_db()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO ratings (draft_id, customer_rating, ts) VALUES (?, ?, ?)",
                (draft_id, customer_rating, _now()),
            )
            return cur.lastrowid
    finally:
        if own:
            conn.close()


def log_send(
    draft_id: str,
    mode: str,
    *,
    reply_message_id: str | None = None,
    confidence: float | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    if mode not in ("auto", "human"):
        raise ValueError(f"mode must be 'auto' or 'human', got {mode!r}")
    own = conn is None
    conn = conn or open_db()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO sends (draft_id, sent_at, mode, reply_message_id, confidence)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    sent_at = excluded.sent_at,
                    mode    = excluded.mode,
                    reply_message_id = excluded.reply_message_id,
                    confidence = excluded.confidence
                """,
                (draft_id, _now(), mode, reply_message_id, confidence),
            )
    finally:
        if own:
            conn.close()


# ---------- readers ----------

def list_drafts(
    *,
    limit: int = 50,
    thread_id: str | None = None,
    intent: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    own = conn is None
    conn = conn or open_db()
    try:
        clauses, args = [], []
        if thread_id:
            clauses.append("thread_id = ?"); args.append(thread_id)
        if intent:
            clauses.append("intent = ?"); args.append(intent)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT id, thread_id, intent, abstained, model, cost_usd, ts FROM drafts {where} "
            f"ORDER BY ts DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def get_full_record(draft_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Return the draft + every edit, rating, and send row attached to it."""
    own = conn is None
    conn = conn or open_db()
    try:
        d = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if not d:
            raise ValueError(f"No draft with id {draft_id!r}")
        edits = conn.execute(
            "SELECT * FROM edits WHERE draft_id = ? ORDER BY id ASC", (draft_id,)
        ).fetchall()
        ratings = conn.execute(
            "SELECT * FROM ratings WHERE draft_id = ? ORDER BY id ASC", (draft_id,)
        ).fetchall()
        send = conn.execute("SELECT * FROM sends WHERE draft_id = ?", (draft_id,)).fetchone()
        draft = dict(d)
        draft["citations"] = json.loads(draft.pop("citations_json"))
        draft["context_window"] = json.loads(draft.pop("context_window_json"))
        return {
            "draft": draft,
            "edits": [dict(r) for r in edits],
            "ratings": [dict(r) for r in ratings],
            "send": dict(send) if send else None,
        }
    finally:
        if own:
            conn.close()
