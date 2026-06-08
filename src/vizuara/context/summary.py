"""One-shot corpus summary — Sonnet condenses the seeded conversations into a
~400-token reference doc the LLM author can prepend to every drafting prompt.

The summary captures:
- House tone (professional / empathetic / concise)
- Common topics and how admin replies have handled them
- Phrasings to reuse and to avoid

Cached to disk; only re-run when the underlying seeded threads change (we
hash the input as a freshness check).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from vizuara import config
from vizuara.cost import get_tracker
from vizuara.cost.pricing import SONNET


SUMMARY_PATH = config.DATA_DIR / "corpus_summary.txt"
META_PATH = config.DATA_DIR / "corpus_summary.meta.json"

MAX_THREADS_FOR_SUMMARY = 100   # seeded threads only; live threads are excluded for stability


SYSTEM_PROMPT = """You distil a customer-support corpus into a single short reference doc.

You will be given many customer messages and admin replies from Lumenx's seeded
demo conversations. Produce a ~350-word doc covering:

1. House tone — adjectives that describe how admins reply (e.g. professional, warm,
   concise, action-oriented).
2. Recurring topics — what customers most commonly ask about.
3. Reply patterns that work — short examples of phrasings the admin team uses
   for common situations (pricing, refunds, integrations, technical issues).
4. Anti-patterns — phrasings or behaviours the admin team avoids.

Output the doc as plain prose with short paragraphs. No markdown headers, no lists,
no preamble like "Here is the summary". Write it in the second-person ("Reply concisely
and...") so the author LLM can use it directly as a style guide."""


def _gather_corpus() -> tuple[str, str]:
    """Return (corpus_text, content_hash)."""
    conn = sqlite3.connect(config.LUMENX_DB_PATH)
    conn.row_factory = sqlite3.Row
    threads = conn.execute(
        """
        SELECT id, intent FROM threads
        WHERE intent IS NOT NULL
        ORDER BY id
        LIMIT ?
        """,
        (MAX_THREADS_FOR_SUMMARY,),
    ).fetchall()

    parts: list[str] = []
    for t in threads:
        msgs = conn.execute(
            """
            SELECT role, text FROM messages
            WHERE thread_id = ? ORDER BY ts ASC
            """,
            (t["id"],),
        ).fetchall()
        if not msgs:
            continue
        thread_block = [f"--- thread {t['id']} (intent: {t['intent']}) ---"]
        for m in msgs:
            thread_block.append(f"{m['role']}: {m['text']}")
        parts.append("\n".join(thread_block))
    conn.close()

    corpus = "\n\n".join(parts)
    h = hashlib.sha256(corpus.encode("utf-8")).hexdigest()
    return corpus, h


def _needs_rebuild(content_hash: str) -> bool:
    if not SUMMARY_PATH.exists() or not META_PATH.exists():
        return True
    try:
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    except Exception:
        return True
    return meta.get("content_hash") != content_hash


def build_summary(force: bool = False) -> str:
    """Compute the corpus summary if missing/stale; return the cached value."""
    corpus, h = _gather_corpus()
    if not force and not _needs_rebuild(h):
        return SUMMARY_PATH.read_text(encoding="utf-8")

    tracker = get_tracker()
    resp = tracker.messages_create(
        stage="context_summary",
        model=SONNET,
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "Here is the Lumenx demo corpus. Distil it into the doc described.\n\n"
                    f"{corpus}"
                ),
            }
        ],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content).strip()

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(text, encoding="utf-8")
    META_PATH.write_text(
        json.dumps(
            {
                "content_hash": h,
                "model": SONNET,
                "input_chars": len(corpus),
                "summary_chars": len(text),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return text


def load_summary() -> str:
    """Return the cached summary, building it on first call if necessary."""
    if SUMMARY_PATH.exists():
        return SUMMARY_PATH.read_text(encoding="utf-8")
    return build_summary()


def main() -> None:
    text = build_summary(force=True)
    print(f"Summary written to {SUMMARY_PATH}  ({len(text)} chars)")
    print("---")
    print(text)


if __name__ == "__main__":
    main()
