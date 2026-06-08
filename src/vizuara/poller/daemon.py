"""Polling daemon: watches /api/admin/inbox and runs the full agent pipeline on new messages.

Safety model:
  - Reads `config.AUTO_SEND_ENABLED` on every iteration. When the master switch
    is OFF (default), the pipeline runs in dry_run mode — drafts are produced
    and logged to feedback.db, but nothing posts to LumenX.
  - When the switch is ON, the router's normal gates apply (sensitive intents
    still go to human review; only high-confidence non-sensitive drafts auto-send).
  - Deduplication: skips any inbox entry whose latest customer_message_id already
    has a draft in feedback.db. Safe to restart.

State (data/poller_state.json):
  - server_time: ISO timestamp returned by the last successful inbox poll,
    used as `since` on the next poll for an incremental cursor.

Run with:  python -m vizuara.poller.daemon
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from vizuara import config
from vizuara.feedback import open_db
from vizuara.lumenx.client import LumenXClient
from vizuara.router import process_message
from vizuara.router.decision import Decision


STATE_PATH = config.DATA_DIR / "poller_state.json"
LOG_PATH = config.DATA_DIR / "poller.log"

POLL_INTERVAL_SEC = float(os.getenv("VIZUARA_POLL_INTERVAL_SEC", "5"))
BACKOFF_MAX_SEC = 300.0
INITIAL_LOOKBACK_HOURS = 24


_shutdown = False


def _request_shutdown(*_args) -> None:
    global _shutdown
    _shutdown = True
    _log("shutdown requested — finishing current iteration")


def _log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} | {msg}"
    print(line, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------- state ----------

def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------- dedupe ----------

def _already_drafted(customer_message_id: str | None) -> bool:
    if not customer_message_id:
        return False
    with open_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM drafts WHERE customer_message_id = ? LIMIT 1",
            (customer_message_id,),
        ).fetchone()
        return row is not None


# ---------- one iteration ----------

def run_once(state: dict[str, Any], *, dry_run_override: bool | None = None) -> dict[str, int]:
    """Run a single inbox poll + process loop. Returns counters."""
    counters = {"entries": 0, "drafted": 0, "skipped_already": 0,
                "skipped_no_customer_msg": 0, "skipped_already_replied": 0,
                "errors": 0, "auto_sent": 0}

    since = state.get("server_time")
    with LumenXClient() as client:
        resp = client.inbox(since=since) if since else client.inbox()

    state["server_time"] = resp.get("server_time")
    entries = resp.get("entries") or []
    counters["entries"] = len(entries)

    for entry in entries:
        thread = entry.get("thread") or {}
        last_cust = entry.get("last_customer_message")
        last_admin = entry.get("last_admin_message")
        thread_id = thread.get("id")
        if not (thread_id and last_cust):
            counters["skipped_no_customer_msg"] += 1
            continue

        msg_id = last_cust.get("id")
        if _already_drafted(msg_id):
            counters["skipped_already"] += 1
            continue

        # If an admin already replied AFTER this customer message, nothing to do.
        if last_admin and last_admin.get("ts") and last_cust.get("ts"):
            if last_admin["ts"] >= last_cust["ts"]:
                counters["skipped_already_replied"] += 1
                continue

        # Per-message safety: dry_run = NOT the master switch (unless overridden).
        dry = (dry_run_override
               if dry_run_override is not None
               else not config.AUTO_SEND_ENABLED)
        try:
            result = process_message(
                thread_id=thread_id,
                customer_message=last_cust.get("text", ""),
                customer_message_id=msg_id,
                dry_run=dry,
            )
            counters["drafted"] += 1
            if result.auto_sent:
                counters["auto_sent"] += 1
            _log(
                f"  drafted thread={thread_id} msg={msg_id} "
                f"intent={result.draft.cited_chunk_ids and result.draft.cited_chunk_ids[0].split(':')[0] or '-'}"
                f" decision={result.route.decision.value}"
                f" reason={result.route.reason.value}"
                f" conf={result.confidence:.3f}"
                f" auto_sent={result.auto_sent}"
            )
        except Exception as exc:
            counters["errors"] += 1
            _log(f"  ERROR processing {thread_id}/{msg_id}: {exc!r}")
            continue

    return counters


# ---------- main loop ----------

def run_loop(*, max_iterations: int | None = None, dry_run_override: bool | None = None) -> None:
    """Forever-loop (or max_iterations for tests). dry_run_override forces dry_run regardless of env."""
    state = _load_state()
    if not state.get("server_time"):
        # First run: start a configurable window in the past so we don't re-process the whole history.
        past = (datetime.now(timezone.utc) - timedelta(hours=INITIAL_LOOKBACK_HOURS)).isoformat()
        state["server_time"] = past
        _log(f"no prior state — starting from {past}")

    _log(f"daemon up | poll_interval={POLL_INTERVAL_SEC}s | "
         f"AUTO_SEND_ENABLED={config.AUTO_SEND_ENABLED} | "
         f"threshold={config.AUTO_SEND_THRESHOLD} | "
         f"dry_run_override={dry_run_override}")

    consecutive_errors = 0
    iters = 0
    while not _shutdown:
        iters += 1
        if max_iterations is not None and iters > max_iterations:
            _log(f"reached max_iterations={max_iterations}, stopping")
            break
        try:
            c = run_once(state, dry_run_override=dry_run_override)
            consecutive_errors = 0
            _save_state(state)
            _log(
                f"poll #{iters} | entries={c['entries']} drafted={c['drafted']} "
                f"skipped_already={c['skipped_already']} "
                f"skipped_replied={c['skipped_already_replied']} "
                f"auto_sent={c['auto_sent']} errors={c['errors']} "
                f"server_time={state.get('server_time')}"
            )
        except Exception as exc:
            consecutive_errors += 1
            backoff = min(BACKOFF_MAX_SEC, POLL_INTERVAL_SEC * (2 ** consecutive_errors))
            _log(f"poll #{iters} FAILED: {exc!r}; backing off {backoff:.1f}s")
            _sleep_with_shutdown(backoff)
            continue

        _sleep_with_shutdown(POLL_INTERVAL_SEC)


def _sleep_with_shutdown(seconds: float) -> None:
    """Sleep but check shutdown flag every 0.5s for responsive Ctrl-C."""
    end = time.monotonic() + seconds
    while not _shutdown and time.monotonic() < end:
        time.sleep(min(0.5, end - time.monotonic()))


# ---------- entry point ----------

def main() -> None:
    signal.signal(signal.SIGINT, _request_shutdown)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, _request_shutdown)
        except Exception:
            pass

    max_iter = None
    dry_override = None
    if "--once" in sys.argv:
        max_iter = 1
    if "--dry-run" in sys.argv:
        dry_override = True
    if "--force-live" in sys.argv:
        dry_override = False

    run_loop(max_iterations=max_iter, dry_run_override=dry_override)


if __name__ == "__main__":
    main()
