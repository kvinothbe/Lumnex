"""Phase 0 smoke test: ping Haiku via the cost tracker and verify cost_log.jsonl grows."""

from __future__ import annotations

from vizuara import config
from vizuara.cost import get_tracker
from vizuara.cost.pricing import HAIKU


def main() -> None:
    tracker = get_tracker()
    resp = tracker.messages_create(
        stage="smoke",
        model=HAIKU,
        max_tokens=32,
        messages=[{"role": "user", "content": "Say 'pong'."}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content)
    print(f"Haiku response: {text.strip()!r}")
    print(f"Cost log:       {config.COST_LOG_PATH}")


if __name__ == "__main__":
    main()
