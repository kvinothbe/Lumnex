"""Build training samples for the confidence MLP.

Source A — seeded threads: for each labeled thread, re-draft the first customer
message without seeing the gold admin reply, then ask Sonnet how similar the
draft is to the gold. The judge score becomes the proxy label.

Source B — adversarial: take real admin replies and mutate them (wrong $,
wrong days, fake products). Label these at 0.0 — they MUST be blocked from
auto-send.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from vizuara import config
from vizuara.author import draft
from vizuara.author.drafter import DraftResult
from vizuara.confidence.features import extract_features, view_of, _DraftView
from vizuara.confidence.judge import judge
from vizuara.context import build_for_message
from vizuara.context.models import ContextWindow


TRAIN_DATA_PATH = config.DATA_DIR / "confidence_train.jsonl"


@dataclass
class TrainingSample:
    source: str                 # "A" | "B"
    thread_id: str
    customer_message: str
    gold_text: str | None
    draft_text: str
    abstained: bool
    cited_chunk_ids: list[str]
    features: list[float]
    label: float
    judge_reason: str | None


def _seeded_first_messages() -> list[tuple[str, str, str, str | None]]:
    """Return (thread_id, customer_message, gold_admin_reply, intent) for the
    100 platform-labeled seeded threads. gold = the first admin reply that
    followed the first customer message."""
    conn = sqlite3.connect(config.LUMENX_DB_PATH)
    conn.row_factory = sqlite3.Row
    threads = conn.execute(
        "SELECT id, intent FROM threads WHERE intent IS NOT NULL ORDER BY id"
    ).fetchall()
    out: list[tuple[str, str, str, str | None]] = []
    for t in threads:
        msgs = conn.execute(
            "SELECT role, text, ts FROM messages WHERE thread_id = ? ORDER BY ts ASC",
            (t["id"],),
        ).fetchall()
        if len(msgs) < 2:
            continue
        first_cust = next((m for m in msgs if m["role"] == "customer"), None)
        if not first_cust:
            continue
        first_admin = next(
            (m for m in msgs if m["role"] == "admin" and m["ts"] > first_cust["ts"]), None
        )
        if not first_admin:
            continue
        out.append((t["id"], first_cust["text"], first_admin["text"], t["intent"]))
    conn.close()
    return out


# ---------- Source A: re-draft + judge ----------

def _one_a(case: tuple[str, str, str, str | None]) -> TrainingSample | tuple[str, Exception]:
    tid, cust, gold, _intent = case
    try:
        ctx = build_for_message(cust, thread_id=tid)
        result = draft(ctx)
        j = judge(cust, gold, result.draft_text)
        return TrainingSample(
            source="A",
            thread_id=tid,
            customer_message=cust,
            gold_text=gold,
            draft_text=result.draft_text,
            abstained=result.abstained,
            cited_chunk_ids=result.cited_chunk_ids,
            features=extract_features(ctx, view_of(result)),
            label=float(j.similarity),
            judge_reason=j.reason,
        )
    except Exception as exc:
        return (tid, exc)


def build_source_a(limit: int | None = None, workers: int = 8) -> list[TrainingSample]:
    cases = _seeded_first_messages()
    if limit:
        cases = cases[:limit]
    print(f"[Source A] re-drafting + judging {len(cases)} seeded threads ({workers} workers)")
    samples: list[TrainingSample] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one_a, c) for c in cases]
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            if isinstance(res, tuple):
                tid, exc = res
                print(f"  [{done}/{len(cases)}] {tid}: SKIPPED — {exc!r}")
                continue
            samples.append(res)
            if done % 10 == 0 or done == len(cases):
                print(f"  [{done}/{len(cases)}] last label={res.label:.2f}")
    return samples


# ---------- Source B: adversarial mutations ----------

_DOLLAR_PAT  = re.compile(r"\$\s?(\d+)")
_DAYS_PAT    = re.compile(r"\b(\d+)\s*-?\s*day(s)?\b", re.IGNORECASE)
_PERCENT_PAT = re.compile(r"\b(\d+)\s?%")

# Realistic-sounding wrong values keep the mutation "plausible" so the MLP must
# rely on the citation/numeric signals, not just on spotting nonsense text.
_WRONG_DAYS    = ["7", "30", "60", "90"]
_WRONG_PERCENT = ["10", "50", "75"]
_FAKE_CLAIMS = [
    "We also offer a lifetime deal at $99 — happy to set you up.",
    "Yes, we have a Discord community you can join right away.",
    "Free trial is now 60 days instead of 14, just enable it in Settings.",
]


def _mutate(text: str, rng: random.Random) -> tuple[str, str] | None:
    """Try to mutate. Returns (mutated_text, mutation_kind) or None if no mutation possible."""
    candidates: list[tuple[str, str]] = []

    m = _DOLLAR_PAT.search(text)
    if m:
        orig = int(m.group(1))
        wrong = orig + rng.choice([10, 20, -5, 30])
        if wrong > 0 and wrong != orig:
            mutated = _DOLLAR_PAT.sub(f"${wrong}", text, count=1)
            candidates.append((mutated, "wrong_dollar"))

    m = _DAYS_PAT.search(text)
    if m:
        orig = m.group(1)
        wrong = rng.choice([d for d in _WRONG_DAYS if d != orig])
        mutated = _DAYS_PAT.sub(f"{wrong} days", text, count=1)
        candidates.append((mutated, "wrong_days"))

    m = _PERCENT_PAT.search(text)
    if m:
        orig = m.group(1)
        wrong = rng.choice([p for p in _WRONG_PERCENT if p != orig])
        mutated = _PERCENT_PAT.sub(f"{wrong}%", text, count=1)
        candidates.append((mutated, "wrong_percent"))

    # Always also offer a "fake claim" mutation — works on any text.
    candidates.append((text.strip() + " " + rng.choice(_FAKE_CLAIMS), "fake_claim"))

    return rng.choice(candidates) if candidates else None


def build_source_b(target_count: int = 50, seed: int = 7) -> list[TrainingSample]:
    rng = random.Random(seed)
    cases = _seeded_first_messages()
    rng.shuffle(cases)
    samples: list[TrainingSample] = []
    for tid, cust, gold, intent in cases:
        if len(samples) >= target_count:
            break
        mutation = _mutate(gold, rng)
        if mutation is None:
            continue
        mutated_text, kind = mutation

        # Build the same context we'd build for the real customer message, so the
        # MLP sees the same retrieval/intent signals — only the draft itself is bad.
        try:
            ctx = build_for_message(cust, thread_id=tid)
        except Exception as exc:
            print(f"  [B] {tid}: context skipped — {exc!r}")
            continue

        # Synthesize a DraftView that looks like an author output but with a hallucinated draft.
        synth = _DraftView(draft_text=mutated_text, abstained=False, cited_chunk_ids=[])
        feats = extract_features(ctx, synth)
        samples.append(
            TrainingSample(
                source="B",
                thread_id=tid,
                customer_message=cust,
                gold_text=gold,
                draft_text=mutated_text,
                abstained=False,
                cited_chunk_ids=[],
                features=feats,
                label=0.0,                  # always low — these have wrong facts
                judge_reason=f"adversarial mutation: {kind}",
            )
        )
    print(f"[Source B] generated {len(samples)} adversarial samples")
    return samples


# ---------- driver ----------

def save_samples(samples: Iterable[TrainingSample], path: Path = TRAIN_DATA_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(asdict(s)) + "\n")
    return path


def load_samples(path: Path = TRAIN_DATA_PATH) -> list[TrainingSample]:
    out: list[TrainingSample] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            out.append(TrainingSample(**d))
    return out


def main() -> None:
    import sys
    a_limit = None
    b_count = 50
    if "--quick" in sys.argv:
        a_limit, b_count = 20, 10
    samples = build_source_a(limit=a_limit) + build_source_b(target_count=b_count)
    p = save_samples(samples)
    print(f"\nWrote {len(samples)} samples to {p}")
    a = sum(1 for s in samples if s.source == "A")
    b = sum(1 for s in samples if s.source == "B")
    print(f"  Source A: {a}    Source B: {b}")
    labels = [s.label for s in samples]
    print(f"  label range: min={min(labels):.2f}  max={max(labels):.2f}  "
          f"mean={sum(labels)/len(labels):.2f}")


if __name__ == "__main__":
    main()
