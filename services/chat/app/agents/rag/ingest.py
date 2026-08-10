"""
RAG write-path (brief §11.1 step 6) — PLACEHOLDER.

Kept separate from the read-path (retriever.py) so chunking/embedding/ingestion
evolves independently. Wire this up when the real vector store lands (embed with
the same model the retriever queries with, upsert into CORPUS_NAMESPACE[corpus]).
"""
from __future__ import annotations

from .retriever import Corpus


def ingest(corpus: Corpus, documents: list[dict]) -> int:
    """Chunk + embed + upsert `documents` into `corpus`. Returns chunk count.

    TODO: implement against the chosen backend (Vertex Vector Search / pgvector).
    Pending BMMB source docs for GUIDELINES_SHARIAH (Sheet 4) and program T&C.
    """
    raise NotImplementedError(
        "RAG ingestion is a placeholder — pending real corpus documents and a "
        "chosen vector backend. See rag/corpora.py CORPUS_NAMESPACE."
    )
