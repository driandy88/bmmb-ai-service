"""
Real retriever backends (brief §11.1 step 3) — PLACEHOLDER implementations.

Both subclass the frozen `Retriever` interface. They construct fine (so
RAG_BACKEND=vertex|pgvector doesn't break startup wiring) but `retrieve()`
raises until wired, rather than silently returning empty results and looking
like "the corpus had nothing". Default remains RAG_BACKEND=stub.

When implementing: embed `query` with the same model used at ingest, query
`namespaces[corpus]`, and map hits -> RetrievalChunk(text, corpus, ref, score,
metadata). Do NOT change the signature — consumers depend on it.
"""
from __future__ import annotations

from app.agents.rag.retriever import Corpus, RetrievalChunk, Retriever
from app.config.settings import Settings


class VertexVectorSearchRetriever(Retriever):
    def __init__(self, settings: Settings, namespaces: dict[Corpus, str]):
        self._settings = settings
        self._namespaces = namespaces

    def retrieve(self, query: str, corpus: Corpus, top_k: int = 5) -> list[RetrievalChunk]:
        raise NotImplementedError(
            "VertexVectorSearchRetriever is a placeholder — implement against "
            "Vertex AI Vector Search, then set RAG_BACKEND=vertex."
        )


class PgVectorRetriever(Retriever):
    def __init__(self, settings: Settings, namespaces: dict[Corpus, str]):
        self._settings = settings
        self._namespaces = namespaces

    def retrieve(self, query: str, corpus: Corpus, top_k: int = 5) -> list[RetrievalChunk]:
        raise NotImplementedError(
            "PgVectorRetriever is a placeholder — implement against Cloud SQL "
            "pgvector, then set RAG_BACKEND=pgvector."
        )
