"""Phase 5 smoke: one full draft -> edit -> rate -> send cycle.

Exit criterion: a single cycle must produce rows in all four tables.
Reads back via get_full_record() and prints a compact summary.
"""

from __future__ import annotations

import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from vizuara.author import draft
from vizuara.context import build_for_message
from vizuara.feedback import get_full_record, list_drafts, log_draft, log_edit, log_rating, log_send


SAMPLE_MESSAGE = "What's the refund window for EmailPilot and how do I cancel?"
SAMPLE_REVIEWER = "vinoth"


def main() -> int:
    print(f"Building context for: {SAMPLE_MESSAGE!r}")
    ctx = build_for_message(SAMPLE_MESSAGE)

    print("Drafting via Sonnet...")
    result = draft(ctx)
    print(f"  draft: {result.draft_text[:120]}...")
    print(f"  cited: {result.cited_chunk_ids}  abstained={result.abstained}")
    print(f"  cost:  ${result.cost_usd:.4f}  (in={result.input_tokens}, out={result.output_tokens}, "
          f"cache_r={result.cache_read_tokens}, cache_w={result.cache_creation_tokens})")

    draft_id = log_draft(ctx, result, customer_message_id="msg-test-12345")
    print(f"  -> logged draft id: {draft_id}")

    # Simulate a light human edit: prepend a personal greeting.
    edited = "Hi Vinoth — " + result.draft_text
    edit_id = log_edit(draft_id, edited, human_reviewer=SAMPLE_REVIEWER)
    print(f"  -> logged edit id: {edit_id}  (slight tweak)")

    # Simulate a customer rating coming back from LumenX.
    rating_id = log_rating(draft_id, customer_rating=5)
    print(f"  -> logged rating id: {rating_id}  (5/5)")

    # Simulate the reviewer hitting send.
    log_send(draft_id, mode="human", reply_message_id="msg-sent-67890")
    print("  -> logged send (mode=human)")

    print("\nReading back full record:\n")
    rec = get_full_record(draft_id)

    fails: list[str] = []
    if not rec["draft"]: fails.append("missing draft row")
    if len(rec["edits"]) != 1: fails.append(f"expected 1 edit, got {len(rec['edits'])}")
    if len(rec["ratings"]) != 1: fails.append(f"expected 1 rating, got {len(rec['ratings'])}")
    if not rec["send"]: fails.append("missing send row")

    d = rec["draft"]
    e = rec["edits"][0]
    r = rec["ratings"][0]
    s = rec["send"]

    print(f"  DRAFT       thread={d['thread_id']}  intent={d['intent']}  cost=${d['cost_usd']:.4f}")
    print(f"              citations={d['citations']}  abstained={bool(d['abstained'])}")
    print(f"              context_window has {len(d['context_window'].get('wiki_chunks', []))} wiki chunks")
    print(f"  EDIT        dist={e['edit_distance']}  ratio={e['edit_ratio']:.3f}  "
          f"accepted_as_is={bool(e['was_accepted_as_is'])}  reviewer={e['human_reviewer']}")
    print(f"  RATING      customer_rating={r['customer_rating']}")
    print(f"  SEND        mode={s['mode']}  reply_message_id={s['reply_message_id']}  at {s['sent_at']}")

    # Sanity: list_drafts() includes our new draft.
    listed = list_drafts(limit=5)
    if not any(row["id"] == draft_id for row in listed):
        fails.append("list_drafts did not include the new draft")
    print(f"\nlist_drafts(limit=5) returned {len(listed)} rows; newest id={listed[0]['id'] if listed else None}")

    if fails:
        print("\nFAILURES:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nAll 4 tables populated. Exit criterion met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
