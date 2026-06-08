"""Wrapper around anthropic.Anthropic that logs every call's cost + tokens."""

from __future__ import annotations

import json
import ssl
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import httpx
import truststore

from vizuara import config
from vizuara.cost.pricing import cost_usd

# Use OS trust store so corporate SSL inspection (or any non-certifi root) works.
_SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class CostTracker:
    """Wraps `anthropic.Anthropic` so every messages.create call lands in cost_log.jsonl.

    Usage:
        tracker = get_tracker()
        resp = tracker.messages_create(
            stage="intent",
            model="claude-haiku-4-5-20251001",
            max_tokens=128,
            system="You classify customer messages.",
            messages=[{"role": "user", "content": "hi"}],
        )
    """

    def __init__(self, log_path: Path | None = None, api_key: str | None = None) -> None:
        self._log_path = log_path or config.COST_LOG_PATH
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        key = api_key or config.ANTHROPIC_API_KEY
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Add it to .env before making Claude calls."
            )
        self._client = anthropic.Anthropic(
            api_key=key,
            http_client=httpx.Client(verify=_SSL_CTX, timeout=60.0),
        )
        self._lock = threading.Lock()

    @property
    def client(self) -> anthropic.Anthropic:
        return self._client

    def messages_create(self, *, stage: str, model: str, **kwargs: Any) -> Any:
        """Call Anthropic messages.create and log the cost. `stage` tags pipeline step.

        Recognised stages: intent, context_summary, author, sentiment, judge, helper.
        """
        resp = self._client.messages.create(model=model, **kwargs)
        self._log_from_response(stage=stage, model=model, resp=resp)
        return resp

    def _log_from_response(self, *, stage: str, model: str, resp: Any) -> None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        usd = cost_usd(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "cost_usd": usd,
            "response_id": getattr(resp, "id", None),
            "stop_reason": getattr(resp, "stop_reason", None),
        }
        with self._lock, self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


_singleton: CostTracker | None = None
_singleton_lock = threading.Lock()


def get_tracker() -> CostTracker:
    """Process-wide singleton so all stages log into the same JSONL."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = CostTracker()
    return _singleton
