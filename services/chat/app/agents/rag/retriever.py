"""
RAG boundary (brief §11.1) — ONE interface, frozen now, so the placeholder can
be swapped for a real vector store by flipping RAG_BACKEND with zero changes to
any agent, node, prompt, or schema.

Freeze this signature:
    retrieve(query: str, corpus: Corpus, top_k: int = 5) -> list[RetrievalChunk]

`StubRetriever` ships today and returns a typed empty list (Sheet 4 corpus is
empty/pending). The real backend (VertexVectorSearchRetriever / PgVectorRetriever
in integrations/vector_search.py) subclasses `Retriever` with the SAME method.
Consumers (program_advisor, guidelines) depend only on this interface and render
`citations` from RetrievalChunk.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Corpus(str, Enum):
    """The three separate retrieval namespaces (brief §3, §11.1)."""
    PROGRAM = "program"                       # Sheet 3.1–3.3 program knowledge / T&C / FAQ
    GUIDELINES_SHARIAH = "guidelines_shariah"  # Sheet 4 (empty — pending BMMB docs)
    SALES_DIR = "sales_dir"                   # Sheet 2 directory (structured; RAG optional)


@dataclass
class RetrievalChunk:
    text: str
    corpus: str
    ref: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class Retriever(ABC):
    @abstractmethod
    def retrieve(self, query: str, corpus: Corpus, top_k: int = 5) -> list[RetrievalChunk]:
        """Return up to top_k chunks for the query from the given corpus."""


class StubRetriever(Retriever):
    """Placeholder — returns a typed empty result so every consumer works and
    renders zero citations until a real index is wired. Swapping in the real
    backend is a one-file addition + `RAG_BACKEND=vertex` (see corpora.py)."""

    def retrieve(self, query: str, corpus: Corpus, top_k: int = 5) -> list[RetrievalChunk]:
        return []
