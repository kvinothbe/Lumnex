"""LLM wiki: structured product knowledge + BM25 retrieval.

Inspired by https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
The wiki is built from the SQLite mirror of LumenX, broken into self-contained
chunks that the LLM author can quote from verbatim.
"""

from vizuara.wiki.chunks import WikiChunk, ScoredChunk
from vizuara.wiki.retrieve import retrieve

__all__ = ["WikiChunk", "ScoredChunk", "retrieve"]
