"""WikiChunk model + canonical chunks built from the SQLite mirror.

A chunk is a self-contained piece of product or company knowledge the LLM
author can quote from. Every chunk carries provenance so the dashboard can
later show exactly which wiki entries fed the draft.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from vizuara import config


@dataclass(frozen=True)
class WikiChunk:
    chunk_id: str          # e.g. "emailpilot:pricing" or "company:refund_window"
    product_id: str | None  # product id, or None for company-wide policies
    section: str           # e.g. "overview", "pricing", "refund", "integrations"
    title: str             # human-readable header, e.g. "EmailPilot — Pricing"
    text: str              # the actual content used both for retrieval and as LLM context


@dataclass(frozen=True)
class ScoredChunk:
    chunk: WikiChunk
    score: float


def _format_pricing(pricing: dict) -> str:
    lines = []
    for tier, details in pricing.items():
        if isinstance(details, dict):
            bits = [f"${details.get('monthly_usd')}/mo"] if details.get("monthly_usd") is not None else []
            if "seats" in details:
                bits.append(f"seats: {details['seats']}")
            if "drafts_per_month" in details:
                bits.append(f"drafts/mo: {details['drafts_per_month']}")
            for k, v in details.items():
                if k not in ("monthly_usd", "seats", "drafts_per_month"):
                    bits.append(f"{k}: {v}")
            lines.append(f"- {tier.capitalize()}: {', '.join(bits)}")
        else:
            lines.append(f"- {tier.capitalize()}: {details}")
    return "\n".join(lines)


def _chunks_from_product(row: sqlite3.Row) -> list[WikiChunk]:
    pid = row["id"]
    name = row["name"]
    features = json.loads(row["features_json"] or "[]")
    pricing = json.loads(row["pricing_json"] or "{}")
    integrations = json.loads(row["integrations_json"] or "[]")

    out: list[WikiChunk] = []

    overview = (
        f"{name} ({row['category']}). Tagline: {row['tagline']}.\n\n{row['description']}"
    )
    out.append(WikiChunk(
        chunk_id=f"{pid}:overview",
        product_id=pid, section="overview",
        title=f"{name} — Overview", text=overview,
    ))

    if features:
        out.append(WikiChunk(
            chunk_id=f"{pid}:features",
            product_id=pid, section="features",
            title=f"{name} — Features",
            text=f"{name} features:\n" + "\n".join(f"- {f}" for f in features),
        ))

    if pricing:
        annual = row["annual_discount_pct"]
        pricing_text = (
            f"{name} pricing and plan costs (USD, monthly):\n{_format_pricing(pricing)}"
        )
        if annual:
            pricing_text += f"\n\nAnnual billing discount: {annual}% off."
        out.append(WikiChunk(
            chunk_id=f"{pid}:pricing",
            product_id=pid, section="pricing",
            title=f"{name} — Pricing", text=pricing_text,
        ))

    if row["refund"]:
        out.append(WikiChunk(
            chunk_id=f"{pid}:refund",
            product_id=pid, section="refund",
            title=f"{name} — Refund policy",
            text=f"{name} refund policy: {row['refund']}",
        ))

    if row["cancellation"]:
        out.append(WikiChunk(
            chunk_id=f"{pid}:cancellation",
            product_id=pid, section="cancellation",
            title=f"{name} — Cancellation",
            text=f"{name} cancellation: {row['cancellation']}",
        ))

    if integrations:
        out.append(WikiChunk(
            chunk_id=f"{pid}:integrations",
            product_id=pid, section="integrations",
            title=f"{name} — Integrations",
            text=f"{name} integrates with: {', '.join(integrations)}.",
        ))

    if row["target_audience"]:
        out.append(WikiChunk(
            chunk_id=f"{pid}:audience",
            product_id=pid, section="audience",
            title=f"{name} — Target audience",
            text=f"{name} is built for: {row['target_audience']}",
        ))

    if row["support_sla_hours"] is not None:
        out.append(WikiChunk(
            chunk_id=f"{pid}:support_sla",
            product_id=pid, section="support_sla",
            title=f"{name} — Support SLA",
            text=f"{name} support SLA: response within {row['support_sla_hours']} hours.",
        ))

    return out


def _chunks_from_company(rows: dict[str, str]) -> list[WikiChunk]:
    company = {k: json.loads(v) if v.startswith(("{", "[", '"')) else v for k, v in rows.items()}
    name = company.get("name", "Company")
    out: list[WikiChunk] = []

    out.append(WikiChunk(
        chunk_id="company:overview",
        product_id=None, section="overview",
        title=f"{name} — Company overview",
        text=(
            f"{name}. {company.get('tagline', '')}\n\n{company.get('description', '')}\n\n"
            f"Founded: {company.get('founded', '?')}. HQ: {company.get('headquarters', '?')}. "
            f"Support: {company.get('support_email', '')} ({company.get('support_hours', '')}). "
            f"Billing currency: {company.get('billing_currency', 'USD')}."
        ),
    ))

    rw = company.get("refund_window_days")
    if rw is not None:
        out.append(WikiChunk(
            chunk_id="company:refund_window",
            product_id=None, section="refund_window",
            title=f"{name} — Company-wide refund window",
            text=(
                f"{name} company-wide refund window: full refund within {rw} days of first "
                f"purchase. This applies across all products unless a product specifies otherwise."
            ),
        ))

    ft = company.get("free_trial_days")
    if ft is not None:
        out.append(WikiChunk(
            chunk_id="company:free_trial",
            product_id=None, section="free_trial",
            title=f"{name} — Free trial",
            text=f"{name} free trial: {ft} days, no credit card required to start.",
        ))

    ad = company.get("annual_discount_pct")
    if ad is not None:
        out.append(WikiChunk(
            chunk_id="company:annual_discount",
            product_id=None, section="annual_discount",
            title=f"{name} — Annual billing discount",
            text=f"{name} annual billing discount: {ad}% off when billed yearly instead of monthly.",
        ))

    startup = company.get("startup_program")
    if isinstance(startup, dict):
        out.append(WikiChunk(
            chunk_id="company:startup_program",
            product_id=None, section="startup_program",
            title=f"{name} — Startup program ({startup.get('name', '')})",
            text=(
                f"{startup.get('name', 'Startup program')}: {startup.get('discount_pct')}% discount "
                f"for {startup.get('duration_months')} months. Eligibility: {startup.get('eligibility', '')}"
            ),
        ))

    edu = company.get("education_program")
    if isinstance(edu, dict):
        out.append(WikiChunk(
            chunk_id="company:education_program",
            product_id=None, section="education_program",
            title=f"{name} — Education program ({edu.get('name', '')})",
            text=(
                f"{edu.get('name', 'Education program')}: {edu.get('discount_pct')}% discount. "
                f"Eligibility: {edu.get('eligibility', '')}"
            ),
        ))

    bundle = company.get("bundle")
    if isinstance(bundle, dict):
        out.append(WikiChunk(
            chunk_id="company:bundle",
            product_id=None, section="bundle",
            title=f"{name} — Bundle offer ({bundle.get('name', '')})",
            text=(
                f"{bundle.get('name', 'Bundle')}: {bundle.get('description', '')} "
                f"Discount: {bundle.get('discount_pct')}% off."
            ),
        ))

    return out


def build_chunks(db_path: Path | None = None) -> list[WikiChunk]:
    """Read products + company from the local SQLite mirror, return all chunks."""
    db_path = db_path or config.LUMENX_DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    products = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    company_rows = dict(conn.execute("SELECT key, value FROM company").fetchall())
    conn.close()

    chunks: list[WikiChunk] = []
    for p in products:
        chunks.extend(_chunks_from_product(p))
    chunks.extend(_chunks_from_company(company_rows))
    return chunks


def chunks_json_path() -> Path:
    return config.DATA_DIR / "wiki" / "chunks.json"


def save_chunks(chunks: list[WikiChunk]) -> Path:
    p = chunks_json_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(c) for c in chunks]
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load_chunks() -> list[WikiChunk]:
    p = chunks_json_path()
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Run `python -m vizuara.wiki.build` first."
        )
    data = json.loads(p.read_text(encoding="utf-8"))
    return [WikiChunk(**d) for d in data]
