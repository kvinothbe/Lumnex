"""Build the wiki: canonical chunks.json + human-readable markdown.

Usage: `python -m vizuara.wiki.build`
Reads from data/lumenx.db, writes to data/wiki/chunks.json and data/wiki/*.md.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from vizuara import config
from vizuara.wiki.chunks import WikiChunk, build_chunks, save_chunks


def _write_product_markdown(product_id: str, chunks: list[WikiChunk], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Title comes from the overview chunk's first line.
    overview = next((c for c in chunks if c.section == "overview"), None)
    title = overview.title.replace(" — Overview", "") if overview else product_id

    body: list[str] = [f"# {title}", ""]
    for c in chunks:
        body.append(f"## {c.title.split(' — ', 1)[1] if ' — ' in c.title else c.section.title()}")
        body.append("")
        body.append(c.text.strip())
        body.append("")
        body.append(f"<!-- chunk_id: {c.chunk_id} -->")
        body.append("")

    path = out_dir / f"{product_id}.md"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _write_policies_markdown(chunks: list[WikiChunk], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    body: list[str] = ["# Lumenx — Company-wide policies", ""]
    for c in chunks:
        title = c.title.split(" — ", 1)[1] if " — " in c.title else c.section.title()
        body.append(f"## {title}")
        body.append("")
        body.append(c.text.strip())
        body.append("")
        body.append(f"<!-- chunk_id: {c.chunk_id} -->")
        body.append("")
    path = out_dir / "policies.md"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def build() -> dict[str, int]:
    chunks = build_chunks()
    chunks_path = save_chunks(chunks)

    wiki_root = config.DATA_DIR / "wiki"
    products_dir = wiki_root / "products"

    by_product: dict[str, list[WikiChunk]] = defaultdict(list)
    company_chunks: list[WikiChunk] = []
    for c in chunks:
        if c.product_id is None:
            company_chunks.append(c)
        else:
            by_product[c.product_id].append(c)

    for pid, pchunks in by_product.items():
        _write_product_markdown(pid, pchunks, products_dir)

    if company_chunks:
        _write_policies_markdown(company_chunks, wiki_root)

    return {
        "chunks": len(chunks),
        "products": len(by_product),
        "company_chunks": len(company_chunks),
        "chunks_json": chunks_path.stat().st_size,
    }


def main() -> None:
    counts = build()
    print(f"Wiki built at {config.DATA_DIR / 'wiki'}")
    for k, v in counts.items():
        print(f"  {k:14s} {v}")


if __name__ == "__main__":
    main()
