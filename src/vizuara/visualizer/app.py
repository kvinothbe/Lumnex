"""FastAPI app: serves the static visualizer + graph / QA / chunk endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from vizuara.visualizer.graph import build_graph, chunk_to_node_id
from vizuara.visualizer.qa import answer
from vizuara.wiki.chunks import load_chunks


HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
TEMPLATES_DIR = HERE / "templates"

app = FastAPI(title="Vizuara — Wiki Visualizer")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _chunks_by_id():
    return {c.chunk_id: c for c in load_chunks()}


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/graph")
def api_graph():
    return JSONResponse(build_graph())


@app.get("/api/chunk/{chunk_id:path}")
def api_chunk(chunk_id: str):
    chunks = _chunks_by_id()
    if chunk_id not in chunks:
        raise HTTPException(status_code=404, detail=f"chunk {chunk_id!r} not found")
    c = chunks[chunk_id]
    return {
        "chunk_id": c.chunk_id,
        "product_id": c.product_id,
        "section": c.section,
        "title": c.title,
        "text": c.text,
        "node_id": chunk_to_node_id(c.chunk_id),
    }


class QueryBody(BaseModel):
    question: str
    k: int = 6


@app.post("/api/query")
def api_query(body: QueryBody):
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="empty question")
    result = answer(body.question, k=body.k)
    return {
        "question": body.question,
        "answer": result.answer,
        "abstained": result.abstained,
        "cited_chunk_ids": result.cited_chunk_ids,
        "cited_node_ids": [chunk_to_node_id(cid) for cid in result.cited_chunk_ids],
        "retrieved": [c.model_dump() for c in result.retrieved],
    }
