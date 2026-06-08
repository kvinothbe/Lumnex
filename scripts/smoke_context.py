"""Phase 3 smoke test: inspect ContextWindow bundles for three representative threads.

Picks one billing (sensitive — should force-include refund chunk), one technical
(non-sensitive — pure retrieval), one greeting (intent short-circuit candidate).
Prints each bundle with full provenance.
"""

from __future__ import annotations

import sqlite3
import sys
import textwrap

# Windows consoles default to cp1252 — force stdout to UTF-8 so smart quotes etc. don't crash.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from vizuara import config
from vizuara.context import build


def _pick_one(intent: str) -> str | None:
    conn = sqlite3.connect(config.LUMENX_DB_PATH)
    row = conn.execute(
        "SELECT id FROM threads WHERE intent = ? ORDER BY id LIMIT 1", (intent,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _dump(ctx) -> None:
    print("=" * 88)
    print(f"thread: {ctx.thread_id}  |  sensitive: {ctx.sensitive}  |  intent: {ctx.intent.intent}")
    print(f"product_hint: {ctx.product_id_hint}  |  est tokens: {ctx.estimated_input_tokens}")
    print(f"customer_message: {ctx.customer_message!r}")
    print(f"retrieval_query: {ctx.retrieval_query!r}")
    print()
    print("THREAD HISTORY:")
    for m in ctx.thread_history:
        body = textwrap.shorten(m.text, width=110, placeholder="…")
        print(f"  [{m.role:8s}] {body}")
    print()
    print(f"WIKI CHUNKS ({len(ctx.wiki_chunks)}):")
    for c in ctx.wiki_chunks:
        tag = "FORCED" if c.forced else f"score={c.score:.2f}"
        body = textwrap.shorten(c.text, width=120, placeholder="…")
        print(f"  - {c.chunk_id:35s} {tag:>14s}  {body}")
    print()
    print(f"CORPUS SUMMARY: {len(ctx.corpus_summary)} chars, {len(ctx.corpus_summary)//4} ~tokens")
    print(f"SIMILAR Q&A:    {len(ctx.similar_qa)} entries (empty until Phase 5)")
    print()


def main() -> int:
    cases = [
        ("billing", "billing -> should force-include refund_window + product refund"),
        ("technical", "technical -> pure retrieval, not sensitive"),
        ("greeting", "greeting -> trivial, just for inspection"),
    ]
    for intent, note in cases:
        tid = _pick_one(intent)
        if not tid:
            print(f"(skipping {intent}: no seeded thread)")
            continue
        print(f"\n>>> {note}\n")
        ctx = build(tid)
        _dump(ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
