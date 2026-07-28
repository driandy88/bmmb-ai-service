"""
Corpus -> backend wiring + the retriever factory (brief §11.1 steps 2 & 4).

Each Corpus member maps to a namespace/index name. Pointing a member at a real
index is all it takes to give it real data — no call-site changes. The factory
picks the implementation from settings (RAG_BACKEND); main.py injects the result
into the agents, which never import or construct a backend themselves.
"""
from __future__ import annotations

from typing import Optional

from app.agents.rag.retriever import Corpus, Retriever, StubRetriever
from app.config.settings import Settings, get_settings
from app.utils.logging import get_logger

log = get_logger("rag")

# Corpus -> (namespace/index name). Real indexes get pointed at here.
CORPUS_NAMESPACE: dict[Corpus, str] = {
    Corpus.PROGRAM: "bmmb-sme-program",
    Corpus.GUIDELINES_SHARIAH: "bmmb-sme-guidelines-shariah",   # pending docs (Sheet 4)
    Corpus.SALES_DIR: "bmmb-sme-sales-dir",
}


def get_retriever(settings: Optional[Settings] = None) -> Retriever:
    settings = settings or get_settings()
    backend = settings.rag_backend
    if backend == "stub":
        return StubRetriever()
    if backend == "vertex":
        from app.integrations.vector_search import VertexVectorSearchRetriever
        return VertexVectorSearchRetriever(settings, CORPUS_NAMESPACE)
    if backend == "pgvector":
        from app.integrations.vector_search import PgVectorRetriever
        return PgVectorRetriever(settings, CORPUS_NAMESPACE)
    log.warning("Unknown RAG_BACKEND=%r; using StubRetriever.", backend)
    return StubRetriever()
