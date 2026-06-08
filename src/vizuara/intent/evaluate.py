"""Evaluate the intent classifier against the 100 platform-labeled seeded threads.

Usage: `python -m vizuara.intent.evaluate`
Exits 0 if exit criteria are met:
  - accuracy >= 95%
  - zero pricing-misclassified-as-greeting failures
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from vizuara import config
from vizuara.intent.router import IntentResult, classify

WORKERS = 10
MIN_ACCURACY = 0.95


def fetch_seeded_cases() -> list[tuple[str, str, str]]:
    """(thread_id, gold_intent, first_customer_message) for every labeled thread."""
    conn = sqlite3.connect(config.LUMENX_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            t.id AS thread_id,
            t.intent AS gold,
            (SELECT m.text FROM messages m
             WHERE m.thread_id = t.id AND m.role = 'customer'
             ORDER BY m.ts ASC LIMIT 1) AS first_msg
        FROM threads t
        WHERE t.intent IS NOT NULL
        ORDER BY t.id
        """
    ).fetchall()
    conn.close()
    return [
        (r["thread_id"], r["gold"], r["first_msg"])
        for r in rows
        if r["first_msg"]
    ]


def _classify_one(case: tuple[str, str, str]) -> tuple[str, str, str, IntentResult | str]:
    thread_id, gold, msg = case
    try:
        return thread_id, gold, msg, classify(msg)
    except Exception as exc:  # surface any single failure rather than killing the batch
        return thread_id, gold, msg, f"ERROR: {exc!r}"


def _print_confusion(confusion: dict[tuple[str, str], int]) -> None:
    golds = sorted({k[0] for k in confusion})
    preds = sorted({k[1] for k in confusion})
    cols = preds  # one column per predicted label that actually appears
    col_w = max(6, max((len(c) for c in cols), default=6))
    label_w = max((len(g) for g in golds), default=6) + 2

    header = " " * label_w + "".join(f"{c[:col_w]:>{col_w + 1}}" for c in cols)
    print(header)
    for g in golds:
        row = f"{g:<{label_w}}"
        for p in cols:
            v = confusion.get((g, p), 0)
            row += f"{v if v else '.':>{col_w + 1}}"
        print(row)


def evaluate() -> dict:
    cases = fetch_seeded_cases()
    n = len(cases)
    if not n:
        print("No seeded threads with intent labels found. Run sync first.")
        sys.exit(2)
    print(f"Classifying {n} seeded threads with {WORKERS} workers...")
    confusion: dict[tuple[str, str], int] = defaultdict(int)
    failures: list[tuple[str, str, str, str]] = []
    correct = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(_classify_one, c) for c in cases]
        for i, fut in enumerate(as_completed(futures), 1):
            thread_id, gold, msg, result = fut.result()
            pred = result.intent if isinstance(result, IntentResult) else str(result)
            confusion[(gold, pred)] += 1
            if pred == gold:
                correct += 1
            else:
                failures.append((thread_id, gold, pred, (msg or "")[:140]))
            if i % 10 == 0 or i == n:
                print(f"  ...{i}/{n} (correct so far: {correct})")

    elapsed = time.time() - t0
    acc = correct / n
    pricing_as_greeting = confusion.get(("pricing", "greeting"), 0)

    print()
    print(f"Confusion matrix (rows = gold, cols = predicted):")
    _print_confusion(confusion)
    print()
    print(f"Accuracy: {correct}/{n} = {acc:.1%}  (took {elapsed:.1f}s)")
    print(f"Pricing-misclassified-as-greeting: {pricing_as_greeting}")

    if failures:
        print(f"\nFailures ({len(failures)}):")
        for tid, gold, pred, msg in failures:
            print(f"  [{tid}] gold={gold:<18s} pred={pred:<18s} msg={msg!r}")

    # Persist results for the dashboard later.
    out_path = config.DATA_DIR / "intent_eval.json"
    out_path.write_text(
        json.dumps(
            {
                "n": n,
                "correct": correct,
                "accuracy": acc,
                "pricing_as_greeting": pricing_as_greeting,
                "elapsed_sec": elapsed,
                "confusion": {f"{g}|{p}": v for (g, p), v in confusion.items()},
                "failures": [
                    {"thread_id": t, "gold": g, "pred": p, "msg": m}
                    for t, g, p, m in failures
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nReport written to {out_path}")

    return {"accuracy": acc, "pricing_as_greeting": pricing_as_greeting}


def main() -> int:
    res = evaluate()
    ok_acc = res["accuracy"] >= MIN_ACCURACY
    ok_pag = res["pricing_as_greeting"] == 0
    if ok_acc and ok_pag:
        print("\nExit criteria met.")
        return 0
    print("\nExit criteria NOT met:")
    if not ok_acc:
        print(f"  - accuracy {res['accuracy']:.1%} < {MIN_ACCURACY:.0%}")
    if not ok_pag:
        print(f"  - {res['pricing_as_greeting']} pricing-as-greeting failures (must be 0)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
