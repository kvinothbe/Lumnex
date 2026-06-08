# Vizuara — Phased Execution Plan

Single source of truth for what's done, what's next, and the exit criteria for each phase. Update the status column as work progresses.

## Status overview

| #  | Phase                              | Status      | Exit criteria                                                                 |
|----|------------------------------------|-------------|-------------------------------------------------------------------------------|
| 0  | Foundation & data pull             | **done**    | `data/lumenx.db` mirrors `/api/admin/export`; cost-tracker scaffold compiles  |
| 1  | LLM Wiki                           | **done**    | One markdown file per product + policies file; retrieval returns top-k for a query |
| 2  | Intent router                      | **done**    | ≥95% accuracy on the 100 seeded threads' labeled intents                      |
| 3  | Context builder                    | **done**    | For a given message, produces a structured context bundle with provenance     |
| 4  | LLM Author (drafter)               | **done**    | Drafts replies for held-out seeded threads; passes anti-hallucination smoke tests |
| 5  | Feedback log + storage             | **done**    | DB schema can record draft, edit, rating, model, tokens, cost                 |
| 6  | Confidence Net (MLP)               | **done**    | Trained model on ≥200 labeled samples; F1 ≥ 0.75 on held-out set              |
| 7  | Router + auto-send (shadow → live) | **infra done** | Infrastructure + simulated shadow done; LIVE shadow week (real customer traffic) is the actual go-live gate and remains pending |
| 8  | Dashboard                          | **done**    | Local FastAPI app shows review queue, cost, expandable contexts               |
| 9  | Polling daemon                     | **infra done** | Loop + dedupe + state + backoff verified end-to-end; 1-day live observation pending (deployment-time activity, gated by Phase 10) |
| 10 | Deployment                         | **package ready** | Dockerfile + railway.json + 3 entrypoints + idempotent bootstrap + DEPLOYMENT.md done; actual `railway up` is the user's step |

---

## Phase 0 — Foundation & data pull

**Goal**: Project skeleton + a local mirror of the LumenX state to develop against.

**Tasks**:
1. Initialize `pyproject.toml` (Python 3.11, deps: `anthropic`, `httpx`, `pydantic`, `python-dotenv`, `sqlmodel` or `sqlite3` direct).
2. Create `src/vizuara/` package layout from CLAUDE.md.
3. `.env.example` with `ANTHROPIC_API_KEY=` and `LUMENX_ADMIN_TOKEN=`. Move the token from `api_description.txt` to `.env`.
4. `vizuara.lumenx.client` — typed wrapper over the admin endpoints.
5. `vizuara.lumenx.sync` — call `/api/admin/export` and write to `data/lumenx.db` (threads, messages, products, policies). Idempotent.
6. `vizuara.cost.tracker` — context-managed wrapper around `anthropic.Anthropic` that logs every call to `data/cost_log.jsonl`.

**Exit criteria**: `python -m vizuara.lumenx.sync` produces a `lumenx.db` with all 212 threads, 511 messages, 20 products. `cost_log.jsonl` records a sample Haiku ping.

**Open question to confirm with user**: confirm Python + SQLite stack OK.

---

## Phase 1 — LLM Wiki

Per Karpathy's gist: the LLM wiki is a small, structured, model-readable knowledge base. We convert the structured product JSON into markdown documents the LLM can quote from verbatim.

**Tasks**:
1. `vizuara.wiki.build` — for each product in `lumenx.db`, emit `data/wiki/products/{product_id}.md` with sections: Overview, Pricing tiers, Refund & cancellation, Integrations, Target audience, Support SLA.
2. Emit `data/wiki/policies.md` from the company-wide policies (refund window, free trial, discounts).
3. `vizuara.wiki.retrieve` — for a query string, return top-k relevant chunks. Start with BM25 (fast, deterministic, no embedding cost). Upgrade to embeddings only if BM25 is insufficient.
4. Each retrieved chunk carries provenance metadata: `(product_id, section, snippet)`.

**Exit criteria**: `retrieve("refund window for EmailPilot")` returns the EmailPilot refund section with provenance. Manual spot-check across 10 queries.

---

## Phase 2 — Intent router

LumenX already has labeled intents on the 100 seeded threads — this is **free ground truth** for evaluation.

**Tasks**:
1. `vizuara.intent.classify(message, recent_history)` — Haiku call with a fixed system prompt and the 10 known categories.
2. Output schema: `{ intent: enum, sub_intent: str|null, confidence: 0-1, product_id_hint: str|null }`.
3. Evaluator: run against the 100 seeded threads' first customer message, compare to the platform's label. Print confusion matrix.
4. Greetings / off-topic → short-circuit and reply directly from a hardcoded polite template (no drafting pass needed).

**Exit criteria**: ≥95% accuracy on the seeded set, no pricing-misclassified-as-greeting failures.

---

## Phase 3 — Context builder

Per the user's instructions, the context window contains:
- Current thread history (full)
- A summary of all past conversations with all past customers (compressed, ~500 tokens)
- LLM-wiki chunks relevant to the query and product
- Similar past Q&A pairs from the feedback log (none yet — empty for now)
- Company policies (always included for sensitive intents)

**Tasks**:
1. `vizuara.context.build(thread_id, message)` — assembles a structured context bundle.
2. `vizuara.context.summarize_corpus()` — one-time Sonnet call that summarizes the 100 seeded conversations into a ~500 token "house style and common themes" doc. Cached.
3. Output bundle is a `ContextWindow` pydantic model with provenance for every piece — so the dashboard can later show what went in.

**Exit criteria**: For a sample message, `build()` produces a bundle the user can inspect end-to-end.

---

## Phase 4 — LLM Author

**Tasks**:
1. `vizuara.author.draft(context_window)` — Sonnet call with a system prompt that hard-codes the anti-hallucination rules.
2. Use prompt caching for the static parts: anti-hallucination system prompt + house-style summary + wiki chunks (these get re-used across requests).
3. Output: `{ draft_text: str, citations: list[{product_id, section}], abstained: bool }`. `abstained=true` means the model declined to answer a pricing/refund question without source data.
4. Smoke tests:
   - Refund question with no wiki match → must abstain.
   - Pricing question with wiki match → must quote the exact tier numbers.
   - Greeting (should have short-circuited at Phase 2, but test direct path too).

**Exit criteria**: 20 hand-picked test cases pass, including 5 adversarial "trick" questions (made-up product name, contradictory pricing claim, etc.).

---

## Phase 5 — Feedback log + storage

This is the durable record of every interaction. It's both the training set for Phase 6 and the source of "similar past Q&A pairs" for the context builder.

**Schema** (`data/feedback.db`):
```
drafts(
  id, thread_id, customer_message_id,
  intent, draft_text, citations_json, abstained,
  context_window_json,    -- full snapshot of what went to the model
  model, input_tokens, cache_read_tokens, cache_creation_tokens,
  output_tokens, cost_usd, ts
)
edits(
  draft_id, final_text, edit_distance, was_accepted_as_is,
  human_reviewer, ts
)
ratings(
  draft_id, customer_rating, ts    -- pulled from LumenX message.rating field
)
sends(
  draft_id, sent_at, mode  -- "auto" | "human"
)
```

**Exit criteria**: A draft → review → edit → send cycle in dev produces complete rows across all four tables.

---

## Phase 6 — Confidence Net (the tiny MLP)

This is the most subtle phase. The user has flagged the bootstrap data problem explicitly.

### Feature vector (per the screenshot's "Tiny MLP")

For each `(message, draft)` pair:
- One-hot intent (10 dims)
- Wiki retrieval top-1 score, top-3 mean score (similarity to query)
- Was the draft an abstention? (1 dim)
- Draft length in tokens (normalized)
- Number of numeric/$/percent mentions in draft (a price-mention sniffer)
- Number of citations the draft made
- Thread depth (n_messages so far)
- Sentiment proxy of customer message (Haiku one-shot, cached per message)
- Presence of urgency keywords (refund / cancel / angry / now) — bag-of-keywords
- Time-of-day, day-of-week
- Customer's prior threads count

Total feature dim ≈ 25–30. Small enough that a 3-layer MLP (30 → 32 → 16 → 1 sigmoid) is more than enough.

### Bootstrap training data — the actual plan

The naive plan ("collect human edits over time") is too slow and the user wants this trained early. Three-pronged bootstrap:

**Source A — seeded conversations (free, ~211 admin replies)**:
- For each existing admin reply in the 100 seeded threads, treat it as the "gold" reply.
- Have the agent re-draft using only the message context (no peek at the admin reply).
- Use a Sonnet "judge" call to score `(gold, draft)` similarity 0–1. This becomes the proxy confidence label.

**Source B — adversarial negatives (~50 samples, generated)**:
- Take a real admin reply, mutate it: change a price by ±20%, swap a refund window, invent a feature.
- These are *low confidence* labeled samples — teach the MLP that hallucinated numbers should score low.
- Feature extractor must catch these via the price-mention sniffer + wiki-citation mismatch.

**Source C — cold-start shadow mode (~100 samples over week 1 of live use)**:
- Real customer messages → agent drafts → human always reviews → log `was_accepted_as_is` and `edit_distance`.
- Label rule: `accepted_as_is → 1.0`, `light edit (dist < 20) → 0.7`, `heavy edit → 0.3`, `rewritten → 0.0`.

Train on A+B initially, fine-tune as C accumulates.

**Exit criteria**: Trained checkpoint with F1 ≥ 0.75 on a held-out 20% of A+B. ROC curve in the dashboard. The user picks the threshold from the ROC.

---

## Phase 7 — Router (shadow → live)

**Tasks**:
1. `vizuara.router.decide(draft, confidence, threshold)` → `"auto"` | `"review"`.
2. **Shadow mode first**: for ≥1 week, route everything to review regardless of confidence. Compare: "what would have been auto-sent" vs what the human actually approved. Build user trust.
3. **Go-live gate**: only after user explicitly approves shadow-mode metrics, flip `AUTO_SEND_ENABLED=true`. Even then, sensitive intents (refund, billing, cancellation) always go through human review regardless of confidence.

**Exit criteria**: Shadow week shows ≥80% of high-confidence drafts would have been accepted without edit. User signs off on go-live.

---

## Phase 8 — Dashboard

FastAPI + Jinja + HTMX. Single binary, runs locally during dev, deployable later.

**Pages**:
- `/` — overview: USD spent today/week, replies/day, auto-sent ratio, avg confidence
- `/queue` — review queue: pending drafts with confidence, draft text, expandable context window, edit + send buttons
- `/replies` — paginated history of all replies with filters (intent, confidence, auto vs human)
- `/replies/{id}` — single reply: full context window, model trace, tokens, cost, edit history, customer rating
- `/costs` — USD over time, breakdown by stage (intent / drafting / summarization / sentiment)
- `/confidence` — MLP performance: ROC, score distribution, threshold tuner

**Exit criteria**: User can review and send a draft end-to-end from the dashboard.

---

## Phase 9 — Polling daemon

**Tasks**:
1. `vizuara.poller` — async loop: every 5s call `/api/admin/inbox?since={last_server_time}`.
2. For each new customer message: run intent → context → draft → confidence → router. Write to feedback log. If router says auto and intent is non-sensitive, call `/api/admin/threads/{id}/reply` with `draft_source="agent"` and the confidence score.
3. Backoff and retries on transient errors.

**Exit criteria**: Run daemon for a day, observe end-to-end flow in dashboard.

---

## Phase 10 — Deployment

**Tasks**:
1. Dockerfile.
2. Railway service (same project as LumenX, separate service).
3. Env vars set in Railway (Anthropic key, LumenX admin token).
4. Persistent volume for `data/`.
5. Dashboard exposed on a subdomain.

**Exit criteria**: One real customer message handled end-to-end in production, visible in dashboard.

---

## Cross-cutting work (do at the start of each phase as needed)

- **Cost logs**: every Claude call goes through `cost.tracker`. No exceptions.
- **Tests**: each module gets pytest tests for the pure logic. LLM calls are mocked.
- **Provenance**: anything shown in the dashboard must link back to the data that produced it.

## Locked decisions (confirmed with user 2026-05-27)

1. **Language**: Python 3.11+
2. **DB**: SQLite (for both LumenX mirror and feedback log)
3. **Dashboard**: FastAPI + Jinja + HTMX
4. **Models**: Haiku 4.5 for intent + helper calls, Sonnet 4.6 for reply drafting. Opus only if Sonnet underperforms.
5. **MLP bootstrap**: seeded threads (re-drafted + Sonnet-judged) + adversarial negatives + shadow-mode online fine-tuning
6. **Auto-send threshold**: deferred to Phase 6 — pick from the ROC curve in the dashboard
7. **Deployment**: Railway alongside LumenX (same region asia-southeast1)
