"""Idempotently mirror the LumenX admin export into a local SQLite file."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from vizuara import config
from vizuara.lumenx.client import LumenXClient
from vizuara.lumenx.schema import ensure_schema, open_db


def _upsert_thread(conn: sqlite3.Connection, t: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO threads (
            id, customer_username, customer_display_name, product_id, intent,
            created_at, updated_at, unread_admin, unread_customer
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            customer_username     = excluded.customer_username,
            customer_display_name = excluded.customer_display_name,
            product_id            = excluded.product_id,
            intent                = excluded.intent,
            created_at            = excluded.created_at,
            updated_at            = excluded.updated_at,
            unread_admin          = excluded.unread_admin,
            unread_customer       = excluded.unread_customer
        """,
        (
            t["id"],
            t.get("customer_username"),
            t.get("customer_display_name"),
            t.get("product_id"),
            t.get("intent"),
            t.get("created_at"),
            t.get("updated_at"),
            t.get("unread_admin") or 0,
            t.get("unread_customer") or 0,
        ),
    )


def _upsert_message(conn: sqlite3.Connection, m: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO messages (id, thread_id, role, text, ts, rating)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            thread_id = excluded.thread_id,
            role      = excluded.role,
            text      = excluded.text,
            ts        = excluded.ts,
            rating    = excluded.rating
        """,
        (
            m["id"],
            m["thread_id"],
            m["role"],
            m["text"],
            m["ts"],
            m.get("rating"),
        ),
    )


def _upsert_product(conn: sqlite3.Connection, p: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO products (
            id, name, category, tagline, description,
            features_json, pricing_json, annual_discount_pct,
            cancellation, refund, integrations_json,
            target_audience, support_sla_hours
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name                = excluded.name,
            category            = excluded.category,
            tagline             = excluded.tagline,
            description         = excluded.description,
            features_json       = excluded.features_json,
            pricing_json        = excluded.pricing_json,
            annual_discount_pct = excluded.annual_discount_pct,
            cancellation        = excluded.cancellation,
            refund              = excluded.refund,
            integrations_json   = excluded.integrations_json,
            target_audience     = excluded.target_audience,
            support_sla_hours   = excluded.support_sla_hours
        """,
        (
            p["id"],
            p["name"],
            p.get("category"),
            p.get("tagline"),
            p.get("description"),
            json.dumps(p.get("features") or []),
            json.dumps(p.get("pricing") or {}),
            p.get("annual_discount_pct"),
            p.get("cancellation"),
            p.get("refund"),
            json.dumps(p.get("integrations") or []),
            p.get("target_audience"),
            p.get("support_sla_hours"),
        ),
    )


def _upsert_company(conn: sqlite3.Connection, company: dict[str, Any]) -> None:
    for k, v in company.items():
        conn.execute(
            """
            INSERT INTO company (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (k, json.dumps(v) if not isinstance(v, str) else v),
        )


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def sync() -> dict[str, int]:
    """Pull the full export and upsert into the local DB. Returns row counts."""
    with LumenXClient() as client:
        export = client.export()

    conn = open_db(config.LUMENX_DB_PATH)
    ensure_schema(conn)

    threads = export.get("threads") or []
    products = export.get("products") or []
    company = export.get("company") or {}

    n_threads = 0
    n_messages = 0
    with conn:
        for t in threads:
            _upsert_thread(conn, t)
            n_threads += 1
            for m in t.get("messages") or []:
                _upsert_message(conn, m)
                n_messages += 1
        for p in products:
            _upsert_product(conn, p)
        _upsert_company(conn, company)
        _set_meta(conn, "last_sync_at", datetime.now(timezone.utc).isoformat())
        _set_meta(conn, "exported_at", export.get("exported_at", ""))

    counts = {
        "threads": n_threads,
        "messages": n_messages,
        "products": len(products),
        "company_keys": len(company),
    }
    conn.close()
    return counts


def main() -> None:
    counts = sync()
    print(f"Synced into {config.LUMENX_DB_PATH}")
    for k, v in counts.items():
        print(f"  {k:14s} {v}")


if __name__ == "__main__":
    main()
