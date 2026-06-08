"""BM25 retrieval over wiki chunks.

Tiny pure-Python BM25 (k1=1.5, b=0.75). No external dependency — these
chunks are small and few (<200), so naive scoring is fine.

Usage from CLI:  `python -m vizuara.wiki.retrieve "refund window for emailpilot"`
Usage from code: `retrieve("how do I cancel?", k=3)`
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from functools import lru_cache

from vizuara.wiki.chunks import ScoredChunk, WikiChunk, load_chunks

_K1 = 1.5
_B = 0.75
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Tiny stoplist — generic English filler words that just add noise.
_STOPWORDS = frozenset({
    "a", "an", "and", "or", "the", "is", "are", "was", "were",
    "be", "been", "being", "to", "of", "in", "on", "for", "at",
    "by", "with", "from", "as", "this", "that", "these", "those",
    "i", "you", "we", "they", "it", "do", "does", "did", "have",
    "has", "had", "can", "could", "would", "should", "will",
    "what", "how", "when", "where", "why", "which", "who",
    "my", "your", "our", "their", "me", "us", "them",
})


def _stem(token: str) -> str:
    """Minimal English singularizer: handles -es, -s plurals. Not a real stemmer."""
    if len(token) > 4 and token.endswith(("ses", "xes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokenize(text: str) -> list[str]:
    return [
        _stem(t)
        for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS and len(t) >= 2
    ]


class _BM25Index:
    """Minimal BM25 over a fixed corpus of chunks."""

    def __init__(self, chunks: list[WikiChunk]) -> None:
        self._chunks = chunks
        # Index a chunk over its full text + its title (titles carry strong intent signal).
        self._docs: list[list[str]] = [_tokenize(c.title + " " + c.text) for c in chunks]
        self._doc_lens = [len(d) for d in self._docs]
        self._avgdl = (sum(self._doc_lens) / len(self._doc_lens)) if self._docs else 0.0
        self._tfs: list[Counter] = [Counter(d) for d in self._docs]
        # Document frequencies for IDF.
        df: Counter = Counter()
        for d in self._docs:
            for term in set(d):
                df[term] += 1
        N = len(self._docs)
        self._idf: dict[str, float] = {
            t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items()
        }

    def score(self, query: str) -> list[ScoredChunk]:
        q_terms = _tokenize(query)
        if not q_terms:
            return []
        scores: list[float] = []
        for i, tf in enumerate(self._tfs):
            s = 0.0
            for term in q_terms:
                if term not in tf:
                    continue
                idf = self._idf.get(term, 0.0)
                f = tf[term]
                denom = f + _K1 * (1 - _B + _B * self._doc_lens[i] / (self._avgdl or 1))
                s += idf * (f * (_K1 + 1)) / denom
            scores.append(s)
        ranked = [
            ScoredChunk(chunk=self._chunks[i], score=scores[i])
            for i in range(len(self._chunks))
            if scores[i] > 0
        ]
        ranked.sort(key=lambda x: x.score, reverse=True)
        return ranked


@lru_cache(maxsize=1)
def _get_index() -> _BM25Index:
    return _BM25Index(load_chunks())


def retrieve(query: str, k: int = 5) -> list[ScoredChunk]:
    """Return the top-k wiki chunks for the query, with BM25 scores and provenance."""
    return _get_index().score(query)[:k]


def reset_index_cache() -> None:
    """Drop the in-process index. Call after wiki rebuild during tests / long-running daemons."""
    _get_index.cache_clear()


def _cli() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m vizuara.wiki.retrieve <query> [k]")
        sys.exit(2)
    query = sys.argv[1]
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    hits = retrieve(query, k)
    if not hits:
        print(f"(no hits for {query!r})")
        return
    print(f"query: {query!r}  (top {len(hits)} of {k} requested)\n")
    for i, h in enumerate(hits, 1):
        snippet = h.chunk.text.strip().replace("\n", " ")
        if len(snippet) > 180:
            snippet = snippet[:177] + "..."
        print(f"  #{i} score={h.score:.3f}  [{h.chunk.chunk_id}]")
        print(f"      {snippet}")
        print()


if __name__ == "__main__":
    _cli()
