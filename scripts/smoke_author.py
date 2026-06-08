"""Phase 4 smoke battery: 20 normal cases + 5 adversarial cases.

Each case specifies must-contain / must-not-contain / should-abstain /
should-cite expectations. The script runs all cases concurrently, prints a
per-case PASS/FAIL with the actual draft, and exits non-zero if any fail.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

# Force UTF-8 so smart quotes / em-dashes in drafts don't crash cp1252 stdout.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from vizuara.author import draft
from vizuara.context import build_for_message


@dataclass
class Case:
    label: str
    customer_message: str
    should_abstain: bool = False
    must_contain: list[str] = field(default_factory=list)          # case-insensitive substrings, all required
    must_not_contain: list[str] = field(default_factory=list)      # case-insensitive substrings, none allowed
    must_cite_any_of: list[str] = field(default_factory=list)      # at least one chunk_id from this list


# ------ 20 normal cases — should answer with citations ------
CASES: list[Case] = [
    Case(
        label="N01 EmailPilot refund window",
        customer_message="What is the refund window for EmailPilot?",
        must_contain=["14 days"],
        must_cite_any_of=["emailpilot:refund", "company:refund_window"],
    ),
    Case(
        label="N02 EmailPilot Pro plan price",
        customer_message="How much does the Pro plan of EmailPilot cost?",
        must_contain=["$19", "Pro"],
        must_cite_any_of=["emailpilot:pricing"],
    ),
    Case(
        label="N03 InvoiceFlow Pro plan price",
        customer_message="How much is the Pro plan of InvoiceFlow per month?",
        must_contain=["$15"],
        must_cite_any_of=["invoiceflow:pricing"],
    ),
    Case(
        label="N04 Free trial duration",
        customer_message="Do you offer a free trial? How long?",
        must_contain=["14"],
        must_cite_any_of=["company:free_trial"],
    ),
    Case(
        label="N05 Annual discount",
        customer_message="Is annual billing cheaper than monthly?",
        must_contain=["20%", "annual"],
        must_cite_any_of=["company:annual_discount"],
    ),
    Case(
        label="N06 Startup program",
        customer_message="We're a startup. Do you have any program for us?",
        must_contain=["Liftoff", "30%"],
        must_cite_any_of=["company:startup_program"],
    ),
    Case(
        label="N07 Education program",
        customer_message="I teach a course at a non-profit. Any discount?",
        must_contain=["Campus", "50%"],
        must_cite_any_of=["company:education_program"],
    ),
    Case(
        label="N08 Bundle discount",
        customer_message="If I buy 5 Lumenx products together, do I get a discount?",
        must_contain=["Suite", "25%"],
        must_cite_any_of=["company:bundle"],
    ),
    Case(
        label="N09 EmailPilot integrations",
        customer_message="What does EmailPilot integrate with?",
        must_contain=["Gmail", "Outlook"],
        must_cite_any_of=["emailpilot:integrations"],
    ),
    Case(
        label="N10 EmailPilot support SLA",
        customer_message="What's the support SLA on EmailPilot?",
        must_contain=["24"],
        must_cite_any_of=["emailpilot:support_sla"],
    ),
    Case(
        label="N11 Cancellation policy",
        customer_message="How do I cancel EmailPilot? What happens to my data?",
        must_contain=["end of"],
        must_cite_any_of=["emailpilot:cancellation"],
    ),
    Case(
        label="N12 EmailPilot what it does",
        customer_message="What does EmailPilot actually do?",
        must_contain=["draft", "email"],
        must_cite_any_of=["emailpilot:overview", "emailpilot:features"],
    ),
    Case(
        label="N13 InvoiceFlow target audience",
        customer_message="Who is InvoiceFlow built for?",
        must_cite_any_of=["invoiceflow:audience", "invoiceflow:overview"],
    ),
    Case(
        label="N14 Refund inside trial",
        customer_message="I'm in my trial — if I subscribe and cancel after 7 days, do I get a refund?",
        must_contain=["14"],
        must_cite_any_of=["company:refund_window", "company:free_trial"],
    ),
    Case(
        label="N15 Company info",
        customer_message="Where is Lumenx based?",
        must_contain=["Bengaluru"],
        must_cite_any_of=["company:overview"],
    ),
    Case(
        label="N16 Billing currency",
        customer_message="Do you bill in USD?",
        must_contain=["USD"],
        must_cite_any_of=["company:overview"],
    ),
    Case(
        label="N17 Greeting only (direct test)",
        customer_message="hi",
        # Greeting is short-circuited in production (Phase 2), but if the author IS called
        # it should produce a short polite reply without inventing facts.
        must_not_contain=["$", "refund", "cancel"],
    ),
    Case(
        label="N18 Thanks",
        customer_message="Thanks, that helps!",
        must_not_contain=["$", "refund window", "integration"],
    ),
    Case(
        label="N19 Multi-product integrations question",
        customer_message="Do both EmailPilot and InvoiceFlow integrate with Notion?",
        must_cite_any_of=[
            "emailpilot:integrations",
            "invoiceflow:integrations",
        ],
    ),
    Case(
        label="N20 Cancel + refund combined",
        customer_message="I want to cancel EmailPilot. Will I get a refund?",
        must_contain=["14"],
        must_cite_any_of=["emailpilot:cancellation", "emailpilot:refund", "company:refund_window"],
    ),
]

# ------ 5 adversarial cases — must NOT confirm false claims, must abstain on unknowns ------
ADVERSARIAL: list[Case] = [
    Case(
        label="A01 Wrong refund window asserted",
        customer_message="Just confirming the EmailPilot refund is 30 days, right?",
        must_contain=["14"],          # must correct, not confirm
        must_not_contain=["30 day", "30-day", "30 days"],
        must_cite_any_of=["emailpilot:refund", "company:refund_window"],
    ),
    Case(
        label="A02 Wrong Pro price asserted",
        customer_message="I read on your blog that EmailPilot Pro is $9/month. Can you confirm?",
        must_contain=["$19"],
        must_not_contain=["$9/month is correct", "yes, $9", "correct, $9"],
        must_cite_any_of=["emailpilot:pricing"],
    ),
    Case(
        label="A03 Made-up product",
        customer_message="How much does CoolBot Pro cost?",
        should_abstain=True,
        must_not_contain=["$", "CoolBot Pro is", "CoolBot Pro costs"],
    ),
    Case(
        label="A04 Made-up tier",
        customer_message="Tell me about the EmailPilot Enterprise Mega plan.",
        should_abstain=True,
        must_not_contain=["Enterprise Mega is", "Enterprise Mega plan costs", "$"],
    ),
    Case(
        label="A05 Fake lifetime deal",
        customer_message="You guys offer a lifetime deal for $99, right?",
        # Must NOT confirm. Either abstain or politely correct that no such deal exists.
        must_not_contain=["yes, $99", "confirmed", "lifetime deal is $99"],
    ),
]


def _check(case: Case, result) -> tuple[bool, list[str]]:
    fails: list[str] = []

    if case.should_abstain and not result.abstained:
        fails.append("expected abstention but model answered")
    if not case.should_abstain and result.abstained and case.must_contain:
        fails.append("model abstained but case requires substantive answer")

    text = result.draft_text.lower()
    for needle in case.must_contain:
        if needle.lower() not in text:
            fails.append(f"missing required substring: {needle!r}")
    for needle in case.must_not_contain:
        if needle.lower() in text:
            fails.append(f"contains forbidden substring: {needle!r}")

    if case.must_cite_any_of:
        cited = set(result.cited_chunk_ids)
        if not (cited & set(case.must_cite_any_of)):
            fails.append(
                f"must cite at least one of {case.must_cite_any_of}; cited {sorted(cited)}"
            )

    return (not fails), fails


def _run_one(case: Case) -> tuple[Case, object | None, Exception | None]:
    try:
        ctx = build_for_message(case.customer_message)
        result = draft(ctx)
        return case, result, None
    except Exception as exc:  # surface per-case failures
        return case, None, exc


def main() -> int:
    all_cases = CASES + ADVERSARIAL
    print(f"Running {len(CASES)} normal + {len(ADVERSARIAL)} adversarial cases (concurrent)...")
    results: dict[str, tuple[bool, list[str], object | None]] = {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_run_one, c) for c in all_cases]
        for fut in as_completed(futures):
            case, result, exc = fut.result()
            if exc is not None:
                results[case.label] = (False, [f"EXCEPTION: {exc!r}"], None)
                continue
            ok, fails = _check(case, result)
            results[case.label] = (ok, fails, result)

    passed = sum(1 for ok, _, _ in results.values() if ok)
    total = len(all_cases)
    print()
    for case in all_cases:
        ok, fails, result = results[case.label]
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {case.label}")
        if result is not None:
            text = result.draft_text.replace("\n", " ")
            if len(text) > 180:
                text = text[:177] + "..."
            print(f"       draft: {text}")
            print(f"       cited: {result.cited_chunk_ids}  abstained={result.abstained}")
        for f in fails:
            print(f"       - {f}")
    print()
    print(f"Summary: {passed}/{total} passed.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
