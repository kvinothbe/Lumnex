"""DDL for the feedback log."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS drafts (
    id                       TEXT PRIMARY KEY,
    thread_id                TEXT NOT NULL,
    customer_message_id      TEXT,
    intent                   TEXT NOT NULL,
    product_id_hint          TEXT,
    draft_text               TEXT NOT NULL,
    abstained                INTEGER NOT NULL DEFAULT 0,   -- 0/1
    citations_json           TEXT NOT NULL,                -- list[chunk_id]
    context_window_json      TEXT NOT NULL,                -- full ContextWindow.model_dump_json()
    model                    TEXT NOT NULL,
    input_tokens             INTEGER,
    cache_read_tokens        INTEGER,
    cache_creation_tokens    INTEGER,
    output_tokens            INTEGER,
    cost_usd                 REAL,
    ts                       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drafts_thread  ON drafts(thread_id);
CREATE INDEX IF NOT EXISTS idx_drafts_intent  ON drafts(intent);
CREATE INDEX IF NOT EXISTS idx_drafts_ts      ON drafts(ts);

-- Human review: every time a reviewer touches the draft. Multiple edits per draft allowed
-- (e.g., reviewer iterates). The MLP trainer in Phase 6 will read the LATEST edit per draft.
CREATE TABLE IF NOT EXISTS edits (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id                 TEXT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    final_text               TEXT NOT NULL,
    edit_distance            INTEGER NOT NULL,
    edit_ratio               REAL NOT NULL,                -- 0..1 similarity (1 = identical)
    was_accepted_as_is       INTEGER NOT NULL,             -- 0/1: edit_distance == 0
    human_reviewer           TEXT,
    ts                       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edits_draft ON edits(draft_id);

-- Customer rating on the message we sent. Pulled from LumenX's message.rating field by the
-- poller in Phase 9. Multiple ratings per draft allowed (customer can update their rating).
CREATE TABLE IF NOT EXISTS ratings (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id                 TEXT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    customer_rating          INTEGER NOT NULL,
    ts                       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ratings_draft ON ratings(draft_id);

-- One send per draft. mode = 'auto' (router auto-sent) | 'human' (reviewer hit send).
CREATE TABLE IF NOT EXISTS sends (
    draft_id                 TEXT PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
    sent_at                  TEXT NOT NULL,
    mode                     TEXT NOT NULL CHECK (mode IN ('auto', 'human')),
    reply_message_id         TEXT,                          -- the LumenX message id returned from POST /reply
    confidence               REAL                           -- the MLP score that gated the auto-send (NULL for human)
);
"""


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()
