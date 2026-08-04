"""A deterministic lexical retriever.

Deliberately simple and deliberately NOT an embedding model. The subject of these
families is the PRODUCT's behaviour when retrieval fails — when the answer is not
in the context, when a plausible distractor is, when the retrieved document is
superseded. A stochastic or opaque retriever would add a second uncontrolled
variable and make paired comparison meaningless.

The retriever is therefore part of the fixed environment, hashed alongside the
corpus, not part of the thing being measured.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..util import stable_hash_text

RETRIEVER_ID = "lexical_overlap_v1"
_STOP = {"the", "a", "an", "in", "of", "for", "to", "and", "or", "is", "are", "with",
         "on", "at", "be", "this", "that", "does", "do", "what", "when", "should", "my"}


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _STOP and len(w) > 2}


@dataclass
class RetrievalResult:
    query: str
    documents: list = field(default_factory=list)     # list[Document]
    scores: dict = field(default_factory=dict)
    corpus_hash: str = ""
    retriever_id: str = RETRIEVER_ID

    def context_text(self) -> str:
        return "\n\n".join(f"[{d.doc_id}] {d.title}\n{d.text}" for d in self.documents)

    def cited_ids(self) -> list:
        return [d.doc_id for d in self.documents]

    def contains_superseded(self) -> bool:
        return any(not d.is_current() for d in self.documents)

    def descriptor(self) -> dict:
        return {"retriever_id": self.retriever_id, "corpus_hash": self.corpus_hash,
                "retrieved": self.cited_ids(), "scores": self.scores,
                "context_hash": stable_hash_text(self.context_text())[:16]}


class Retriever:
    def __init__(self, corpus, k: int = 3):
        self.corpus = corpus
        self.k = k

    def retrieve(self, query: str) -> RetrievalResult:
        q = _tokens(query)
        scored = []
        for d in self.corpus.documents:
            t = _tokens(d.title + " " + d.text)
            overlap = len(q & t)
            if overlap:
                scored.append((overlap / (len(q) or 1), d))
        # Deterministic tie-break by doc_id so identical inputs give identical context.
        scored.sort(key=lambda p: (-p[0], p[1].doc_id))
        top = scored[: self.k]
        return RetrievalResult(
            query=query, documents=[d for _s, d in top],
            scores={d.doc_id: round(s, 4) for s, d in top},
            corpus_hash=self.corpus.bundle_hash())
