"""Simulated shadow-week evaluation — no new LLM cost.

Replays the Phase 6 bootstrap samples (`data/confidence_train.jsonl`) through
the live MLP and the router at several candidate thresholds. Reports, per
threshold:

  - auto-send rate         (of all messages, how many would have auto-sent?)
  - acceptance rate        (of those auto-sent, how many had judge similarity >= 0.7?)
  - hallucination leak     (of those auto-sent, how many were adversarial Source B?)
  - sensitive blocked      (count of billing/cancellation correctly held for human review)

Exit criterion from PLAN.md: shadow shows >= 80% of high-confidence drafts
would have been accepted (judge similarity >= 0.7) AND zero adversarial samples
leak through. The script PRINTS this per threshold so the user can pick.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from vizuara import config
from vizuara.confidence.bootstrap import TRAIN_DATA_PATH, load_samples
from vizuara.confidence.mlp import infer, load
from vizuara.intent.router import INTENTS
from vizuara.router.decision import SENSITIVE_INTENTS, Decision, decide


ACCEPT_BAR = 0.7              # judge similarity >= this is "would be accepted"
THRESHOLDS = [0.30, 0.40, 0.50, 0.60, 0.70]


def _intent_from_features(feats: list[float]) -> str:
    """Decode the one-hot intent from the first 11 features."""
    head = feats[: len(INTENTS)]
    return INTENTS[max(range(len(head)), key=lambda i: head[i])]


def main() -> int:
    if not TRAIN_DATA_PATH.exists():
        print(f"ERROR: bootstrap data missing at {TRAIN_DATA_PATH}.")
        print("Run `python -m vizuara.confidence.bootstrap` first.")
        return 2

    ckpt_path = config.DATA_DIR / "confidence.pt"
    if not ckpt_path.exists():
        print(f"ERROR: checkpoint missing at {ckpt_path}.")
        print("Run `python -m vizuara.confidence.train` first.")
        return 2

    samples = load_samples(TRAIN_DATA_PATH)
    print(f"Replaying {len(samples)} bootstrap samples "
          f"({sum(1 for s in samples if s.source == 'A')} A + "
          f"{sum(1 for s in samples if s.source == 'B')} B)")

    model, meta = load(ckpt_path)
    print(f"Checkpoint val F1 (training time): {meta.get('val_at_0.5', {}).get('f1', 0):.3f}\n")

    # Score every sample once.
    enriched = []
    for s in samples:
        score = infer(model, s.features)
        intent = _intent_from_features(s.features)
        enriched.append({
            "source": s.source,
            "intent": intent,
            "label": s.label,
            "score": score,
            "abstained": s.abstained,
            "thread_id": s.thread_id,
        })

    # Per-threshold reports.
    rows = []
    print(f"{'thr':>5}  {'auto%':>7}  {'accept%':>8}  {'leak':>5}  {'sens_blk':>8}  {'review%':>8}")
    print("-" * 60)
    for thr in THRESHOLDS:
        autos = 0
        accepted = 0
        adv_leak = 0
        sens_blocked = 0
        reviews = 0
        for x in enriched:
            r = decide(
                intent=x["intent"],
                abstained=x["abstained"],
                confidence=x["score"],
                threshold=thr,
                auto_send_enabled=True,
            )
            if r.decision == Decision.AUTO:
                autos += 1
                if x["label"] >= ACCEPT_BAR:
                    accepted += 1
                if x["source"] == "B":
                    adv_leak += 1
            else:
                reviews += 1
                if x["intent"] in SENSITIVE_INTENTS:
                    sens_blocked += 1
        n = len(enriched)
        rows.append({
            "threshold": thr,
            "auto_rate": autos / n,
            "acceptance_rate_among_auto": (accepted / autos) if autos else 0.0,
            "hallucination_leak_count": adv_leak,
            "sensitive_blocked": sens_blocked,
            "review_rate": reviews / n,
            "auto_count": autos,
            "accepted_count": accepted,
        })
        accept_pct = (accepted / autos * 100) if autos else 0
        print(f"{thr:>5.2f}  {autos/n*100:>6.1f}%  {accept_pct:>7.1f}%  "
              f"{adv_leak:>5d}  {sens_blocked:>8d}  {reviews/n*100:>7.1f}%")

    print()
    print("Legend:")
    print("  thr       — auto-send threshold")
    print("  auto%     — fraction of messages the router would have auto-sent")
    print("  accept%   — of those, fraction with judge similarity >= 0.7")
    print("  leak      — adversarial (Source B, wrong-facts) drafts that slipped through")
    print("  sens_blk  — count of sensitive (billing/cancellation) intents held for human review")
    print()

    # Persist for the dashboard.
    out = config.DATA_DIR / "shadow_eval.json"
    out.write_text(
        json.dumps({"accept_bar": ACCEPT_BAR, "thresholds": rows}, indent=2),
        encoding="utf-8",
    )
    print(f"Report: {out}")

    # Exit-criterion check at default threshold.
    default_thr = config.AUTO_SEND_THRESHOLD
    row = next((r for r in rows if abs(r["threshold"] - default_thr) < 1e-6), None)
    if row is None:
        row = min(rows, key=lambda r: abs(r["threshold"] - default_thr))
    print()
    print(f"At configured threshold {row['threshold']:.2f}:")
    print(f"  auto-send rate        = {row['auto_rate']*100:.1f}%")
    print(f"  accept-if-sent rate   = {row['acceptance_rate_among_auto']*100:.1f}%")
    print(f"  hallucination leaks   = {row['hallucination_leak_count']}")

    ok_acceptance = row["acceptance_rate_among_auto"] >= 0.80
    ok_leak = row["hallucination_leak_count"] == 0
    if ok_acceptance and ok_leak:
        print("\nSimulated shadow-week exit criteria MET at the configured threshold.")
        print("Real shadow-week (Phase 7 final gate) still requires running with real")
        print("customer messages for >=1 week and explicit user approval.")
        return 0
    print("\nSimulated shadow-week exit criteria NOT met at the configured threshold:")
    if not ok_acceptance:
        print(f"  - acceptance rate {row['acceptance_rate_among_auto']*100:.1f}% < 80%")
    if not ok_leak:
        print(f"  - {row['hallucination_leak_count']} adversarial sample(s) leaked through")
    print("Try a higher threshold from the table above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
