"""Build a node-edge knowledge graph from the wiki chunks + raw product data.

Nodes:
- 1 company node (Lumenx)
- N product nodes (colored by category)
- K category nodes (the distinct product categories)
- M integration nodes (Slack, Gmail, ...) — only those used by 2+ products are shown,
  so the graph has real "cross-reference" edges instead of long unique tails.
- P policy nodes (refund window, free trial, annual discount, ...)

Edges:
- product -> category
- product -> integration (shared)
- product -> company
- policy  -> company
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from typing import Any

from vizuara import config
from vizuara.wiki.chunks import load_chunks


# --- color palette (kept in one place so the frontend doesn't have to know it) ---
CATEGORY_COLORS: dict[str, str] = {
    "Communication": "#4cc9f0",
    "Productivity":  "#f72585",
    "Operations":    "#7209b7",
    "Finance":       "#ffb703",
    "Sales":         "#06d6a0",
    "Analytics":     "#ff006e",
    "Developer":     "#3a86ff",
    "Marketing":     "#fb5607",
    "HR":            "#8ecae6",
}
COMPANY_COLOR     = "#ffd166"
INTEGRATION_COLOR = "#9d4edd"
POLICY_COLOR      = "#ff7b00"
DEFAULT_PRODUCT   = "#8338ec"


def _category_color(cat: str | None) -> str:
    if not cat:
        return DEFAULT_PRODUCT
    return CATEGORY_COLORS.get(cat, DEFAULT_PRODUCT)


def _load_products() -> list[dict[str, Any]]:
    conn = sqlite3.connect(config.LUMENX_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    conn.close()
    products = []
    for r in rows:
        products.append(
            {
                "id": r["id"],
                "name": r["name"],
                "category": r["category"],
                "tagline": r["tagline"],
                "integrations": json.loads(r["integrations_json"] or "[]"),
            }
        )
    return products


def _company_name() -> str:
    conn = sqlite3.connect(config.LUMENX_DB_PATH)
    row = conn.execute("SELECT value FROM company WHERE key='name'").fetchone()
    conn.close()
    if not row:
        return "Lumenx"
    v = row[0]
    return v.strip('"') if v.startswith('"') else v


def build_graph() -> dict[str, list[dict]]:
    products = _load_products()
    chunks = load_chunks()
    company = _company_name()

    chunks_by_product: dict[str, list[dict]] = defaultdict(list)
    chunks_by_section: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        key = c.product_id or "company"
        chunks_by_product[key].append(
            {"chunk_id": c.chunk_id, "section": c.section, "title": c.title}
        )
        chunks_by_section[c.section].append({"chunk_id": c.chunk_id, "product_id": c.product_id})

    nodes: list[dict] = []
    edges: list[dict] = []

    # Company node.
    nodes.append({
        "id": "company:lumenx",
        "label": company,
        "type": "company",
        "color": COMPANY_COLOR,
        "size": 70,
        "chunks": chunks_by_product["company"],
    })

    # Category nodes (one per distinct category).
    categories = sorted({p["category"] for p in products if p["category"]})
    for cat in categories:
        nodes.append({
            "id": f"category:{cat}",
            "label": cat,
            "type": "category",
            "color": _category_color(cat),
            "size": 40,
            "chunks": [],
        })

    # Product nodes.
    for p in products:
        nodes.append({
            "id": f"product:{p['id']}",
            "label": p["name"],
            "type": "product",
            "category": p["category"],
            "tagline": p["tagline"],
            "color": _category_color(p["category"]),
            "size": 30,
            "chunks": chunks_by_product[p["id"]],
        })
        # product → category
        if p["category"]:
            edges.append({
                "source": f"product:{p['id']}",
                "target": f"category:{p['category']}",
                "type": "belongs_to",
            })
        # product → company (subtle backbone edge)
        edges.append({
            "source": f"product:{p['id']}",
            "target": "company:lumenx",
            "type": "part_of",
        })

    # Integration nodes — only those used by 2+ products (real cross-references).
    integration_counts: Counter[str] = Counter()
    for p in products:
        for it in p["integrations"]:
            integration_counts[it] += 1
    shared_ints = {it for it, n in integration_counts.items() if n >= 2}
    for it in sorted(shared_ints):
        nodes.append({
            "id": f"integration:{it}",
            "label": it,
            "type": "integration",
            "color": INTEGRATION_COLOR,
            "size": 22,
            "chunks": [],
        })
    for p in products:
        for it in p["integrations"]:
            if it in shared_ints:
                edges.append({
                    "source": f"product:{p['id']}",
                    "target": f"integration:{it}",
                    "type": "integrates_with",
                })

    # Policy nodes (each company-wide chunk that isn't the overview).
    for c in chunks:
        if c.product_id is not None or c.chunk_id == "company:overview":
            continue
        nodes.append({
            "id": f"policy:{c.section}",
            "label": c.title.split(" — ", 1)[-1],
            "type": "policy",
            "color": POLICY_COLOR,
            "size": 26,
            "chunks": [{"chunk_id": c.chunk_id, "section": c.section, "title": c.title}],
        })
        edges.append({
            "source": f"policy:{c.section}",
            "target": "company:lumenx",
            "type": "policy_of",
        })

    return {"nodes": nodes, "edges": edges}


def chunk_to_node_id(chunk_id: str) -> str:
    """Map a wiki chunk to the graph node id it belongs to."""
    if chunk_id.startswith("company:"):
        section = chunk_id.split(":", 1)[1]
        if section == "overview":
            return "company:lumenx"
        return f"policy:{section}"
    product_id = chunk_id.split(":", 1)[0]
    return f"product:{product_id}"
