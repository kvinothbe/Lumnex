"""Phase 1 smoke test: 10 retrieval queries against the wiki.

Each row asserts the top-1 chunk_id matches an expected pattern. Prints a
green PASS / red FAIL per query and a final summary so you can spot-check.
"""

from __future__ import annotations

import re
import sys

from vizuara.wiki.retrieve import retrieve


CASES: list[tuple[str, str]] = [
    ("What is the refund window for EmailPilot?",      r"^emailpilot:(refund|cancellation)$"),
    ("How much does the Pro plan of InvoiceFlow cost?", r"^invoiceflow:pricing$"),
    ("Does TaskGrid integrate with Slack?",             r"^taskgrid:integrations$"),
    ("What is the Lumenx company-wide refund policy?",  r"^company:refund_window$"),
    ("Do you have a free trial?",                       r"^company:free_trial$"),
    ("annual billing discount across products",         r"^company:annual_discount$"),
    ("startup program eligibility",                     r"^company:startup_program$"),
    ("How do I cancel my EmailPilot subscription?",     r"^emailpilot:(cancellation|refund)$"),
    ("Which audience is EmailPilot built for?",         r"^emailpilot:audience$"),
    ("Support SLA for EmailPilot",                      r"^emailpilot:support_sla$"),
]


def main() -> int:
    passed = 0
    failures: list[str] = []
    for query, expected_pat in CASES:
        hits = retrieve(query, k=3)
        if not hits:
            failures.append(f"  FAIL: {query!r} -> no hits")
            print(f"  FAIL: {query!r} -> no hits")
            continue
        top_id = hits[0].chunk.chunk_id
        ok = re.match(expected_pat, top_id) is not None
        tag = "PASS" if ok else "FAIL"
        print(f"  {tag}: {query!r}")
        print(f"        top1={top_id!r} score={hits[0].score:.2f}  (want ~{expected_pat})")
        if not ok:
            failures.append(f"{query!r}: got {top_id!r}, wanted {expected_pat!r}")
        else:
            passed += 1
    print()
    print(f"Summary: {passed}/{len(CASES)} passed.")
    if failures:
        print("Failures:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
