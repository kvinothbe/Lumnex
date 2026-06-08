# Vizuara — Auto-Reply Agent for LumenX

This repository builds an auto-reply LLM agent that drafts customer support replies on the LumenX SaaS demo platform, with a confidence-gated auto-send mechanism, a feedback loop, and a cost/observability dashboard.

The target deployment is the user's LumenX instance at `https://lumenx-demo.up.railway.app`.

## High-level architecture (one pipeline, four small parts)

```
incoming msg ─► Intent Router ─► Context Builder ─► LLM Author ─► Confidence Net ─► Router ─► auto-send | human review
                  (Haiku)         (wiki + history)    (Sonnet)       (tiny MLP)        ▲                       │
                                                                                       └────── feedback log ◄──┘
```

1. **Intent Router** — cheap LLM (Haiku) classifies the query (greeting, pricing, refund, technical, integration, billing, cancellation, feature, compare-competitor, multi-product, other). Greetings / off-topic get answered without a drafting pass.
2. **Context Builder** — assembles: current thread history, summary of past conversations across all customers, retrieved LLM-wiki entries for the relevant product, similar past Q&A pairs from the feedback log, company policies.
3. **LLM Author** — higher-capability LLM (Sonnet) drafts the reply under a strict anti-hallucination system prompt. Pricing and refund details must come from retrieved wiki content; absent that the model must say "Sorry, I don't have access to that information."
4. **Confidence Net** — a small PyTorch MLP, trained on past (features → was-accepted-as-is) pairs, predicts a 0–1 confidence score for the draft.
5. **Router** — if `confidence > threshold` (user-tunable) the draft is auto-sent via `POST /api/admin/threads/{id}/reply`; else it lands in a human review queue.
6. **Feedback log** — every draft, every human edit, every customer rating gets recorded. This corpus retrains the MLP and feeds future context.

## Repository layout (target, after Phase 1)

```
vizuara/
├── CLAUDE.md            ← this file
├── README.md            ← user-facing docs
├── PLAN.md              ← phased execution plan (single source of truth for progress)
├── api_description.txt  ← LumenX API + admin token (DO NOT commit token to a public repo)
├── instructions.txt     ← original requirements from user
├── pyproject.toml       ← Python package config
├── .env.example         ← template for ANTHROPIC_API_KEY, LUMENX_ADMIN_TOKEN
├── .gitignore
├── src/vizuara/
│   ├── lumenx/          ← LumenX API client + sync to local SQLite
│   ├── wiki/            ← LLM wiki: product knowledge + retrieval
│   ├── intent/          ← intent router
│   ├── context/         ← context builder
│   ├── author/          ← reply drafter
│   ├── confidence/      ← MLP train/infer
│   ├── router/          ← auto-send vs review decision
│   ├── feedback/        ← draft + edit + rating logging
│   ├── cost/            ← per-call token + USD accounting
│   ├── dashboard/       ← FastAPI app: review queue, costs, contexts
│   └── poller/          ← background daemon that watches /api/admin/inbox
└── data/                ← local SQLite, MLP checkpoints (gitignored)
```

## Stack decisions (locked unless user changes them)

- **Language**: Python 3.11+
- **LLM SDK**: `anthropic` (official). Aggressive prompt caching for static wiki + system prompts.
- **Models**:
  - `claude-haiku-4-5` for intent routing and any cheap helper calls
  - `claude-sonnet-4-6` for reply drafting (Opus only if Sonnet quality is insufficient)
- **Storage**: SQLite (single file, matches LumenX's own choice)
- **MLP**: PyTorch, small fully-connected net (≤ 3 hidden layers, ≤ 64 units each)
- **Dashboard**: FastAPI + Jinja + HTMX (light, server-rendered, fast)
- **Background work**: asyncio polling loop
- **Deployment**: Railway (same provider as LumenX) — final phase

## Anti-hallucination rules (non-negotiable in system prompts)

- Never invent pricing, refund windows, free-trial durations, plan tiers, or discount percentages. If the wiki doesn't have it, say so.
- Never invent integrations, SLAs, or feature lists.
- When the question is pricing/refund/cancellation related, the draft MUST cite the product id whose wiki entry supplied the number.
- Tone: professional, empathetic, concise. No emojis unless the customer used one first.

## Cost discipline (user is explicit about this)

- Every Claude API call MUST flow through `vizuara.cost.track(...)` which records: model, input_tokens, cache_read_tokens, cache_creation_tokens, output_tokens, USD cost, and which pipeline stage made the call.
- Dashboard shows USD/day, USD/reply, and per-reply token breakdown.
- Each reply in the dashboard has an expandable "context window" view showing exactly what was sent to the model.

## Working on this project

- **PLAN.md is the source of truth for progress.** Update it after each phase.
- Phases are sequential. Do not start phase N+1 until N's exit criteria are met.
- Never commit `.env`, `data/*.db`, or `*.pt` checkpoints.
- Never auto-send a real reply during development. Auto-send goes live only after explicit user approval in the Phase 7 review.
- The LumenX admin token in `api_description.txt` is a real production token for the demo instance — treat it as a secret. Move it to `.env` before any commit.

## See also

- `PLAN.md` — phase-by-phase execution plan
- `README.md` — user-facing project documentation
- `instructions.txt` — original requirements
- `api_description.txt` — LumenX API endpoints and auth
