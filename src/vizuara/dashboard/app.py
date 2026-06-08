"""Vizuara dashboard FastAPI app."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from vizuara import config
from vizuara.dashboard import queries
from vizuara.feedback import log_edit, log_send
from vizuara.lumenx.client import LumenXClient

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
TEMPLATES_DIR = HERE / "templates"

app = FastAPI(title="Vizuara — Operator Dashboard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Mount the wiki-explorer (visualizer) as a sub-app at /wiki so it's always
# available on the same domain — no separate Railway service or URL needed.
# The visualizer makes its own asset/API paths prefix-aware via root_path.
from vizuara.visualizer.app import app as wiki_app  # noqa: E402

app.mount("/wiki", wiki_app)


@app.get("/healthz")
def healthz():
    """Liveness probe for Railway. Deliberately touches no DB or data files so it
    returns 200 the instant uvicorn is up — even while the background bootstrap is
    still building the volume on first boot."""
    return {"status": "ok"}


def _nav(active: str) -> dict:
    return {
        "active": active,
        "auto_send_enabled": config.AUTO_SEND_ENABLED,
        "auto_send_threshold": config.AUTO_SEND_THRESHOLD,
        # Default to the in-process /wiki mount; allow an external override.
        "wiki_url": config.WIKI_EXPLORER_URL or "/wiki/",
    }


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def page_overview(request: Request):
    stats = queries.overview_stats()
    cost = queries.cost_by_stage(days=7)
    return templates.TemplateResponse(
        request, "overview.html",
        {"nav": _nav("overview"), "stats": stats, "cost": cost},
    )


@app.get("/queue", response_class=HTMLResponse)
def page_queue(request: Request):
    pending = queries.list_pending(limit=100)
    return templates.TemplateResponse(
        request, "queue.html",
        {"nav": _nav("queue"), "pending": pending},
    )


@app.get("/replies", response_class=HTMLResponse)
def page_replies(request: Request, intent: str | None = None, sent_only: bool = False):
    rows = queries.list_drafts_history(limit=200, intent=intent, sent_only=sent_only)
    intents = queries.distinct_intents()
    return templates.TemplateResponse(
        request, "replies.html",
        {"nav": _nav("replies"), "rows": rows, "intents": intents,
         "filter_intent": intent, "filter_sent_only": sent_only},
    )


@app.get("/replies/{draft_id}", response_class=HTMLResponse)
def page_reply_detail(request: Request, draft_id: str):
    rec = queries.get_draft_detail(draft_id)
    if not rec:
        raise HTTPException(status_code=404, detail="draft not found")
    return templates.TemplateResponse(
        request, "reply_detail.html",
        {"nav": _nav("replies"), "rec": rec},
    )


@app.get("/costs", response_class=HTMLResponse)
def page_costs(request: Request, days: int = 7):
    cost = queries.cost_by_stage(days=days)
    return templates.TemplateResponse(
        request, "costs.html",
        {"nav": _nav("costs"), "cost": cost, "days": days},
    )


@app.get("/confidence", response_class=HTMLResponse)
def page_confidence(request: Request):
    rep = queries.confidence_report()
    return templates.TemplateResponse(
        request, "confidence.html",
        {"nav": _nav("confidence"), "rep": rep},
    )


# ---------- queue actions (POST forms) ----------

@app.post("/queue/{draft_id}/send")
def queue_send(draft_id: str, final_text: str = Form(...), live: str = Form(default="false")):
    rec = queries.get_draft_detail(draft_id)
    if not rec:
        raise HTTPException(404, "draft not found")
    # Record the edit if the human changed the text.
    original = rec["draft"]["draft_text"]
    if final_text.strip() != original.strip():
        log_edit(draft_id, final_text, human_reviewer="dashboard")

    sent_id = None
    if live.lower() == "true":
        thread_id = rec["draft"]["thread_id"]
        if thread_id.startswith(("synthetic", "seed-", "shadow-")):
            raise HTTPException(
                400,
                f"refusing to live-send to thread_id={thread_id!r} — looks synthetic. "
                f"Use a real LumenX thread or keep live=false.",
            )
        with LumenXClient() as client:
            resp = client.reply(thread_id, final_text, draft_source="human")
        sent_id = resp.get("id") or resp.get("message_id")

    log_send(draft_id, mode="human", reply_message_id=sent_id)
    return RedirectResponse(url="/queue", status_code=303)


# ---------- json APIs ----------

@app.get("/api/overview")
def api_overview():
    return JSONResponse(queries.overview_stats())


@app.get("/api/costs")
def api_costs(days: int = 7):
    return JSONResponse(queries.cost_by_stage(days=days))


@app.get("/api/confidence")
def api_confidence():
    return JSONResponse(queries.confidence_report())


@app.get("/api/queue")
def api_queue():
    return JSONResponse({"pending": queries.list_pending(limit=200)})
