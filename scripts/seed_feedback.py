"""Seed feedback.db with ~8 representative drafts so the dashboard has data.

Runs each message through the full pipeline (dry_run=True so nothing posts to
LumenX), then simulates a human edit + send on a couple of them. Skips drafts
whose customer_message already exists in the DB so it can be re-run safely.
"""

from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from vizuara.feedback import log_edit, log_rating, log_send, open_db
from vizuara.router import process_message


# (customer_message, thread_id, mark_as_sent, simulated_edit_text_or_None)
SEEDS: list[tuple[str, str, bool, str | None]] = [
    # auto-eligible: clean answer with citation, NOT sensitive
    ("Does EmailPilot work with Gmail and Notion?", "seed-emailpilot-int", True, None),
    ("What's the annual billing discount?", "seed-annual", True, None),

    # sensitive — will always route to review
    ("Cancel my subscription. Will I get a refund?", "seed-cancel", False, None),
    ("My last invoice has the wrong currency", "seed-billing", False, None),

    # pricing — non-sensitive but threshold-gated
    ("How much is InvoiceFlow Pro per month?", "seed-pricing", False, None),

    # off-topic — low confidence
    ("Can you recommend a good restaurant in Bangalore?", "seed-offtopic", False, None),

    # adversarial — should be low confidence
    ("Confirm the EmailPilot refund window is 30 days, right?", "seed-adversarial", False, None),

    # technical
    ("File upload on EmailPilot keeps failing at 80%", "seed-tech", False,
     "Hey, sorry for the trouble — could you tell me which browser and workspace, and whether it still fails in incognito? I'll dig into the logs on my end in parallel."),
]


def _already_seeded(customer_message: str) -> bool:
    with open_db() as conn:
        row = conn.execute(
            "SELECT id FROM drafts WHERE thread_id LIKE 'seed-%' AND context_window_json LIKE ?",
            (f'%{customer_message[:60]}%',),
        ).fetchone()
        return row is not None


def main() -> int:
    print(f"Seeding {len(SEEDS)} drafts into feedback.db (dry_run=True throughout)\n")
    for msg, tid, mark_sent, edit_text in SEEDS:
        if _already_seeded(msg):
            print(f"  SKIP (already seeded): {msg!r}")
            continue
        print(f"  drafting: {msg!r}")
        # Force auto_send_enabled=True so the router gates fire as they would in prod,
        # but dry_run=True so no LumenX POST happens.
        r = process_message(
            thread_id=tid,
            customer_message=msg,
            use_synthetic_context=True,
            dry_run=True,
            auto_send_enabled=True,
            threshold=0.5,
        )
        print(f"    -> draft_id={r.draft_id[:8]}  conf={r.confidence:.3f}  "
              f"decision={r.route.decision.value}  reason={r.route.reason.value}")

        if edit_text:
            log_edit(r.draft_id, edit_text, human_reviewer="vinoth")
            print(f"    -> simulated human edit")
        if mark_sent:
            # Simulate the human reviewer pressing send on a clean draft.
            log_send(r.draft_id, mode="human", reply_message_id=f"sim-{r.draft_id[:6]}")
            log_rating(r.draft_id, customer_rating=5)
            print(f"    -> simulated send (mode=human) + 5/5 rating")

    print("\nDone seeding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
