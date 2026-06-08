"""USD per 1M tokens for each Claude model we use.

Update these constants when Anthropic changes pricing. The cost tracker
multiplies (tokens / 1_000_000) by the per-MTok price.

Cache-read and cache-write multipliers follow Anthropic's documented schedule
for the 5-minute ephemeral cache:
  - cache_read  = 0.10 x input
  - cache_write = 1.25 x input
"""

from __future__ import annotations

from typing import TypedDict


class ModelPrice(TypedDict):
    input_per_mtok: float
    output_per_mtok: float


# Default model IDs locked in CLAUDE.md (Haiku 4.5 + Sonnet 4.6, Opus only on demand).
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"
OPUS = "claude-opus-4-7"


# Per-MTok USD prices. Best-guess from the Claude 4 family pricing as of early 2026;
# verify against the Anthropic pricing page before publishing the cost dashboard.
PRICES: dict[str, ModelPrice] = {
    HAIKU:  {"input_per_mtok": 1.0,  "output_per_mtok": 5.0},
    SONNET: {"input_per_mtok": 3.0,  "output_per_mtok": 15.0},
    OPUS:   {"input_per_mtok": 15.0, "output_per_mtok": 75.0},
}

CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Compute USD cost from a Claude API usage block."""
    p = PRICES.get(model)
    if p is None:
        # Unknown model: try a prefix match so dated suffixes still resolve.
        for k, v in PRICES.items():
            if model.startswith(k.split("-202")[0]):
                p = v
                break
    if p is None:
        return 0.0  # Unknown model: log zero rather than crash; caller can see the JSONL.
    inp = p["input_per_mtok"]
    out = p["output_per_mtok"]
    total = (
        input_tokens * inp
        + output_tokens * out
        + cache_read_tokens * inp * CACHE_READ_MULTIPLIER
        + cache_creation_tokens * inp * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000.0
    return total
