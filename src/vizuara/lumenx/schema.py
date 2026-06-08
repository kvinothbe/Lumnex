"""DDL for the local LumenX mirror in SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS threads (
    id                    TEXT PRIMARY KEY,
    customer_username     TEXT,
    customer_display_name TEXT,
    product_id            TEXT,
    intent                TEXT,
    created_at            TEXT,
    updated_at            TEXT,
    unread_admin          INTEGER DEFAULT 0,
    unread_customer       INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_threads_intent     ON threads(intent);
CREATE INDEX IF NOT EXISTS idx_threads_product    ON threads(product_id);
CREATE INDEX IF NOT EXISTS idx_threads_updated_at ON threads(updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id        TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role      TEXT NOT NULL,   -- 'customer' | 'admin'
    text      TEXT NOT NULL,
    ts        TEXT NOT NULL,
    rating    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_messages_thread_ts ON messages(thread_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_role      ON messages(role);

CREATE TABLE IF NOT EXISTS products (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    category            TEXT,
    tagline             TEXT,
    description         TEXT,
    features_json       TEXT,
    pricing_json        TEXT,
    annual_discount_pct INTEGER,
    cancellation        TEXT,
    refund              TEXT,
    integrations_json   TEXT,
    target_audience     TEXT,
    support_sla_hours   INTEGER
);

-- Company-wide policies and metadata stored as flat key/value JSON for flexibility.
CREATE TABLE IF NOT EXISTS company (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Sync bookkeeping: last successful pull, server_time for incremental polling.
CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
