# Vizuara

A confidence-gated auto-reply agent for the [LumenX](https://lumenx-demo.up.railway.app) SaaS demo support inbox.

Vizuara drafts replies to incoming customer messages, scores its own confidence with a small neural network, auto-sends the high-confidence ones, and routes the rest to a human reviewer — with a feedback loop that retrains the confidence model over time.

## Why this exists

LumenX is a multi-product SaaS storefront with a built-in customer chat. Today every reply is hand-written by an admin. Vizuara watches the inbox, drafts the obvious replies (pricing, refund policy, feature questions), gets out of the way for the tricky ones, and never invents numbers it can't cite.

## Architecture

```
incoming msg ─► Intent Router ─► Context Builder ─► LLM Author ─► Confidence Net ─► Router ─► auto-send | human review
                  (Haiku)         (wiki + history)    (Sonnet)       (tiny MLP)        ▲                       │
                                                                                       └────── feedback log ◄──┘
```

Four small components, one pipeline:

- **Intent Router** — cheap Haiku call. 10-way classifier (pricing, refund, technical, integration, billing, cancellation, feature, compare-competitor, multi-product, greeting). Greetings short-circuit; everything else goes to the drafter.
- **Context Builder** — pulls the current thread, a compressed summary of past conversations across all customers, the LLM-wiki entry for the relevant product, similar past Q&A pairs from the feedback log, and company-wide policies.
- **LLM Author** — Sonnet drafts the reply under a strict system prompt: no inventing prices, refund windows, or features. If the wiki doesn't have it, the model is required to say "Sorry, I don't have access to that information."
- **Confidence Net** — a small PyTorch MLP (~30 input features, two hidden layers) predicts whether the draft is good enough to send without review.
- **Router** — above the user-set threshold → auto-send; below → human review queue. Refund / billing / cancellation intents always go through review regardless of score.
- **Feedback log** — every draft, every human edit, every customer rating is logged. The MLP retrains on this corpus periodically; the context builder retrieves from it on similar future questions.

## The confidence net, in one paragraph

A neural network that decides whether each draft is safe to auto-send. Inputs: intent class, retrieval similarity scores, draft length, whether the draft cited the wiki, whether it mentioned prices, sentiment of the customer message, and a few thread-shape features. Trained initially on a bootstrap dataset built from (a) the 100 seeded conversations on LumenX, (b) deliberately mutated "bad" replies as negative samples, then (c) fine-tuned online from real human edits during shadow-mode operation. Threshold is the user's choice — the dashboard shows the ROC curve and you pick.

## LLM Wiki

Per Karpathy's [LLM wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), the LLM wiki is a small, structured, model-readable knowledge base. Vizuara converts LumenX's product JSON into one markdown file per product plus a policies file, then retrieves the relevant chunks at draft-time and passes them to the author with prompt caching.

## Cost discipline

Every Claude API call routes through a cost tracker that logs model, input tokens, cache-read tokens, cache-creation tokens, output tokens, and USD cost — tagged with which pipeline stage made the call. The dashboard surfaces:

- USD per day, USD per reply
- Per-reply token breakdown
- An expandable "context window" view for every reply, showing exactly what was sent to the model

Defaults: `claude-haiku-4-5` for routing and cheap helper calls, `claude-sonnet-4-6` for the drafting itself. Opus only if Sonnet quality is insufficient.

## Anti-hallucination guarantees

Pricing, refund windows, free-trial durations, plan tiers, discount percentages, integrations, SLAs — none of these may be invented by the author. The system prompt requires the model to cite the product wiki entry it pulled the number from, or to abstain. Sensitive intents (refund, billing, cancellation) are always routed to human review regardless of confidence score.

## Status

This project builds in phases. See [`PLAN.md`](./PLAN.md) for the full execution plan with exit criteria for each phase. As of writing, none of the phases have started — the user is reviewing the plan.

## Quick links

- **Plan**: [PLAN.md](./PLAN.md)
- **Project-level Claude instructions**: [CLAUDE.md](./CLAUDE.md)
- **LumenX API reference**: [api_description.txt](./api_description.txt)
- **Original requirements**: [instructions.txt](./instructions.txt)
- **LumenX customer chat**: https://lumenx-demo.up.railway.app/chat
- **LumenX admin UI**: https://lumenx-demo.up.railway.app/admin

## Getting started (after Phase 0 lands)

```bash
# 1. Install
pip install -e .

# 2. Configure
cp .env.example .env
# fill in ANTHROPIC_API_KEY and LUMENX_ADMIN_TOKEN

# 3. Pull LumenX state into local SQLite
python -m vizuara.lumenx.sync

# 4. Build the LLM wiki from products
python -m vizuara.wiki.build

# 5. Run the dashboard
uvicorn vizuara.dashboard.app:app --reload
```
