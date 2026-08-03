"""
RAG boundary (brief §11.1) — ONE interface, frozen now, so the placeholder can
be swapped for a real vector store by flipping RAG_BACKEND with zero changes to
any agent, node, prompt, or schema.

Base signature (unchanged for every existing caller):
    retrieve(query: str, corpus: Corpus, top_k: int = 5) -> list[RetrievalChunk]

Evolved ADDITIVELY for §6/§6a — new params are keyword-only with defaults, so the
existing agent calls `retrieve(message, Corpus.PROGRAM, top_k=3)` are untouched
("fix the interface, not the callers", §6):
  * `corpus` may be a single Corpus OR an iterable of Corpus (AMB-05 needs
    program + guidelines_shariah together).
  * `program_code` scopes to one product (§6a branch A/B); None = unscoped.
  * `channel` is the access-tier boundary — "customer" filters access_tier in SQL
    so internal criteria never leak (§11); "internal" sees everything.

`StubRetriever` ships today and returns a typed empty list. The real backend
(PgVectorRetriever in integrations/vector_search.py) subclasses `Retriever` with
the SAME method. Consumers (program_advisor, guidelines) render `citations` from
RetrievalChunk and are unaffected by the additions.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union


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


# A single corpus or a set of them (AMB-05 spans program + guidelines_shariah).
CorpusScope = Union[Corpus, Iterable[Corpus]]


class Retriever(ABC):
    @abstractmethod
    def retrieve(
        self,
        query: str,
        corpus: CorpusScope,
        top_k: int = 5,
        *,
        program_code: str | None = None,
        channel: str = "customer",
    ) -> list[RetrievalChunk]:
        """Return up to top_k chunks for the query from the given corpus/corpora,
        optionally scoped to one program and filtered to an access-tier channel."""


class StubRetriever(Retriever):
    """Placeholder — returns a typed empty result so every consumer works and
    renders zero citations until a real index is wired. Swapping in the real
    backend is a one-file addition + `RAG_BACKEND=pgvector` (see corpora.py)."""

    def retrieve(
        self,
        query: str,
        corpus: CorpusScope,
        top_k: int = 5,
        *,
        program_code: str | None = None,
        channel: str = "customer",
    ) -> list[RetrievalChunk]:
        return []
