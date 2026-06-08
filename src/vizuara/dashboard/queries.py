"""Read-side helpers that aggregate data from feedback.db, cost_log.jsonl, and eval JSONs."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from vizuara import config
from vizuara.feedback import open_db


# ---------- queue ----------

def list_pending(*, limit: int = 50) -> list[dict[str, Any]]:
    """Drafts that haven't been sent yet (any decision/reason)."""
    with open_db() as conn:
        rows = conn.execute(
            """
            SELECT d.*
            FROM drafts d
            LEFT JOIN sends s ON s.draft_id = d.id
            WHERE s.draft_id IS NULL
            ORDER BY d.ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_decorate_draft_row(r, conn) for r in rows]


def list_drafts_history(
    *,
    limit: int = 100,
    intent: str | None = None,
    sent_only: bool = False,
) -> list[dict[str, Any]]:
    with open_db() as conn:
        clauses, args = [], []
        join = "LEFT JOIN sends s ON s.draft_id = d.id"
        if intent:
            clauses.append("d.intent = ?"); args.append(intent)
        if sent_only:
            clauses.append("s.draft_id IS NOT NULL")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT d.*, s.mode AS send_mode, s.sent_at AS sent_at "
            f"FROM drafts d {join} {where} "
            f"ORDER BY d.ts DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [_decorate_draft_row(r, conn) for r in rows]


def get_draft_detail(draft_id: str) -> dict[str, Any] | None:
    with open_db() as conn:
        d = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if not d:
            return None
        edits = conn.execute(
            "SELECT * FROM edits WHERE draft_id = ? ORDER BY id ASC", (draft_id,)
        ).fetchall()
        ratings = conn.execute(
            "SELECT * FROM ratings WHERE draft_id = ? ORDER BY id ASC", (draft_id,)
        ).fetchall()
        send = conn.execute("SELECT * FROM sends WHERE draft_id = ?", (draft_id,)).fetchone()

        draft = dict(d)
        draft["citations"] = json.loads(draft.pop("citations_json"))
        draft["context_window"] = json.loads(draft.pop("context_window_json"))
        return {
            "draft": draft,
            "edits": [dict(r) for r in edits],
            "ratings": [dict(r) for r in ratings],
            "send": dict(send) if send else None,
        }


def _decorate_draft_row(row, conn) -> dict[str, Any]:
    d = dict(row)
    # Pull the latest edit's final_text if any (for queue display)
    e = conn.execute(
        "SELECT final_text, edit_distance FROM edits WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
        (d["id"],),
    ).fetchone()
    d["latest_edit_text"] = e["final_text"] if e else None
    d["latest_edit_distance"] = e["edit_distance"] if e else None
    d["citations"] = json.loads(d.pop("citations_json"))
    cw = json.loads(d.pop("context_window_json"))
    # Surface a few useful preview fields without re-parsing the whole CW upstream.
    d["customer_message"] = cw.get("customer_message", "")
    d["intent_confidence"] = cw.get("intent", {}).get("confidence")
    d["product_id_hint"] = cw.get("product_id_hint")
    d["sensitive"] = cw.get("sensitive", False)
    d["wiki_chunks_count"] = len(cw.get("wiki_chunks") or [])
    return d


# ---------- overview ----------

def overview_stats() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(days=1)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()

    with open_db() as conn:
        total_drafts = conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
        drafts_day  = conn.execute("SELECT COUNT(*) FROM drafts WHERE ts >= ?", (day_ago,)).fetchone()[0]
        drafts_week = conn.execute("SELECT COUNT(*) FROM drafts WHERE ts >= ?", (week_ago,)).fetchone()[0]
        cost_day = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM drafts WHERE ts >= ?", (day_ago,)).fetchone()[0]
        cost_week = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM drafts WHERE ts >= ?", (week_ago,)).fetchone()[0]
        sent_total = conn.execute("SELECT COUNT(*) FROM sends").fetchone()[0]
        sent_auto  = conn.execute("SELECT COUNT(*) FROM sends WHERE mode='auto'").fetchone()[0]
        sent_human = conn.execute("SELECT COUNT(*) FROM sends WHERE mode='human'").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM drafts d LEFT JOIN sends s ON s.draft_id = d.id WHERE s.draft_id IS NULL"
        ).fetchone()[0]
        abstained = conn.execute("SELECT COUNT(*) FROM drafts WHERE abstained=1").fetchone()[0]

        intents = dict(conn.execute(
            "SELECT intent, COUNT(*) FROM drafts GROUP BY intent ORDER BY 2 DESC"
        ).fetchall())

        edit_rows = conn.execute("SELECT was_accepted_as_is, edit_distance FROM edits").fetchall()
        accepted_as_is = sum(1 for r in edit_rows if r["was_accepted_as_is"])
        n_edits = len(edit_rows)

        ratings = [r[0] for r in conn.execute("SELECT customer_rating FROM ratings").fetchall()]

    return {
        "total_drafts": total_drafts,
        "drafts_day": drafts_day,
        "drafts_week": drafts_week,
        "cost_day_usd": float(cost_day or 0),
        "cost_week_usd": float(cost_week or 0),
        "sent_total": sent_total,
        "sent_auto": sent_auto,
        "sent_human": sent_human,
        "pending_review": pending,
        "abstained": abstained,
        "auto_send_pct": (100 * sent_auto / sent_total) if sent_total else 0,
        "intents": intents,
        "edits_total": n_edits,
        "accepted_as_is_pct": (100 * accepted_as_is / n_edits) if n_edits else 0,
        "rating_count": len(ratings),
        "avg_rating": (sum(ratings) / len(ratings)) if ratings else 0,
    }


# ---------- cost ----------

def cost_by_stage(*, days: int = 7) -> dict[str, Any]:
    """Aggregate cost_log.jsonl by stage over the last N days, plus a daily series."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    per_stage = defaultdict(lambda: {"calls": 0, "usd": 0.0, "in": 0, "out": 0, "cache_r": 0, "cache_w": 0})
    per_day_usd: dict[str, float] = defaultdict(float)
    total_usd = 0.0

    path = config.COST_LOG_PATH
    if not path.exists():
        return {"per_stage": {}, "per_day": [], "total_usd": 0.0, "days": days}

    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("ts", "") < cutoff:
                continue
            stage = e.get("stage", "unknown")
            usd = float(e.get("cost_usd", 0))
            per_stage[stage]["calls"] += 1
            per_stage[stage]["usd"] += usd
            per_stage[stage]["in"] += int(e.get("input_tokens") or 0)
            per_stage[stage]["out"] += int(e.get("output_tokens") or 0)
            per_stage[stage]["cache_r"] += int(e.get("cache_read_tokens") or 0)
            per_stage[stage]["cache_w"] += int(e.get("cache_creation_tokens") or 0)
            day = e["ts"][:10]
            per_day_usd[day] += usd
            total_usd += usd

    per_day = [{"date": d, "usd": round(per_day_usd[d], 4)} for d in sorted(per_day_usd)]
    return {
        "per_stage": {k: {**v, "usd": round(v["usd"], 4)} for k, v in per_stage.items()},
        "per_day": per_day,
        "total_usd": round(total_usd, 4),
        "days": days,
    }


# ---------- confidence ----------

def confidence_report() -> dict[str, Any]:
    eval_path = config.DATA_DIR / "confidence_eval.json"
    shadow_path = config.DATA_DIR / "shadow_eval.json"
    eval_data = json.loads(eval_path.read_text(encoding="utf-8")) if eval_path.exists() else None
    shadow_data = json.loads(shadow_path.read_text(encoding="utf-8")) if shadow_path.exists() else None
    return {"eval": eval_data, "shadow": shadow_data,
            "current_threshold": config.AUTO_SEND_THRESHOLD,
            "auto_send_enabled": config.AUTO_SEND_ENABLED}


def distinct_intents() -> list[str]:
    with open_db() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT intent FROM drafts ORDER BY intent"
        ).fetchall()]
