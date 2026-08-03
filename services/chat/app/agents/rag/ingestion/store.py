"""
Stage 5 — persist documents and their chunks (RAG_PLAN phase 3).

The only module that writes to `rag_documents` / `rag_chunks`. Two things here
are load-bearing:

**Vectors are passed as a literal cast.** pg8000 has no adapter for pgvector's
`vector` type, so embeddings are sent as `'[0.1,0.2,…]'` with an explicit
`CAST(... AS vector)`. That keeps the write path dependency-free and works
identically on Cloud SQL and a plain Postgres.

**A document's chunks are replaced, never merged.** Re-ingesting deletes the
old chunk rows and inserts the new set inside one transaction. Merging would
require reconciling chunk boundaries that may have shifted for unrelated
reasons (a changed `RAG_CHUNK_TOKENS` re-cuts the whole document), and a
half-updated document is worse than a briefly absent one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Sequence

import sqlalchemy

from app.agents.rag.db import get_engine
from app.agents.rag.ingestion.chunker import Chunk
from app.agents.rag.retriever import Corpus
from app.utils.logging import get_logger

log = get_logger("rag.store")


@dataclass(frozen=True)
class DocumentRecord:
    id: int
    source_uri: str
    content_hash: str
    # Which model embedded this document's chunks, or None when there are no
    # chunks or they disagree (a half-finished run). The ingestion pipeline
    # compares this with the active embedder: unchanged bytes are not enough to
    # justify skipping if the vectors came from a different model.
    embedding_model: Optional[str] = None
    chunk_count: int = 0


def to_vector_literal(values: Sequence[float]) -> str:
    """Render an embedding in pgvector's text input format."""
    return "[" + ",".join(str(float(value)) for value in values) + "]"


class ChunkStore:
    """Read/write access to the RAG tables."""

    def __init__(self, engine: Optional[sqlalchemy.engine.Engine] = None):
        self._engine = engine or get_engine()

    # ── reads ────────────────────────────────────────────────────────────────

    # `min(embedding_model)` with a distinct-count guard: one model -> report
    # it; zero or several -> report None, so the pipeline treats the document
    # as needing a re-embed rather than trusting a mixed set of vectors.
    _DOCUMENT_COLUMNS = """
        d.id, d.source_uri, d.content_hash,
        (SELECT count(*) FROM rag_chunks c WHERE c.document_id = d.id) AS chunk_count,
        (SELECT CASE WHEN count(DISTINCT c.embedding_model) = 1
                     THEN min(c.embedding_model) END
           FROM rag_chunks c WHERE c.document_id = d.id) AS embedding_model
    """

    @staticmethod
    def _to_record(row) -> DocumentRecord:
        return DocumentRecord(
            id=row.id,
            source_uri=row.source_uri,
            content_hash=row.content_hash,
            embedding_model=row.embedding_model,
            chunk_count=row.chunk_count,
        )

    def find_document(self, corpus: Corpus, source_uri: str) -> Optional[DocumentRecord]:
        sql = sqlalchemy.text(f"""
            SELECT {self._DOCUMENT_COLUMNS}
              FROM rag_documents d
             WHERE d.corpus = :corpus AND d.source_uri = :source_uri
        """)
        with self._engine.connect() as conn:
            row = conn.execute(sql, {"corpus": corpus.value, "source_uri": source_uri}).first()
        return self._to_record(row) if row else None

    def list_documents(self, corpus: Corpus) -> list[DocumentRecord]:
        sql = sqlalchemy.text(f"""
            SELECT {self._DOCUMENT_COLUMNS}
              FROM rag_documents d
             WHERE d.corpus = :corpus ORDER BY d.source_uri
        """)
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"corpus": corpus.value}).all()
        return [self._to_record(row) for row in rows]

    def stats(self, corpus: Corpus) -> dict:
        sql = sqlalchemy.text("""
            SELECT count(DISTINCT d.id) AS documents,
                   count(c.id)          AS chunks,
                   count(c.embedding)   AS embedded
              FROM rag_documents d
              LEFT JOIN rag_chunks c ON c.document_id = d.id
             WHERE d.corpus = :corpus
        """)
        with self._engine.connect() as conn:
            row = conn.execute(sql, {"corpus": corpus.value}).one()
        return {"documents": row.documents, "chunks": row.chunks, "embedded": row.embedded}

    # ── writes ───────────────────────────────────────────────────────────────

    def write_document(
        self,
        *,
        corpus: Corpus,
        source_uri: str,
        title: str,
        content_hash: str,
        byte_size: int,
        page_count: Optional[int],
        metadata: dict,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        embedding_model: str,
    ) -> int:
        """Upsert a document and replace its chunks. Returns the document id.

        One transaction for the whole document: either the new chunk set is
        live or the old one still is. A partially-replaced document would serve
        a mix of two revisions, which is the one outcome worth ruling out.
        """
        if len(chunks) != len(vectors):
            raise ValueError(
                f"{len(chunks)} chunks but {len(vectors)} vectors for {source_uri}."
            )

        document_sql = sqlalchemy.text("""
            INSERT INTO rag_documents
                   (corpus, source_uri, title, content_hash, byte_size, page_count, metadata)
            VALUES (:corpus, :source_uri, :title, :content_hash, :byte_size, :page_count,
                    CAST(:metadata AS jsonb))
            ON CONFLICT (corpus, source_uri) DO UPDATE
                SET title        = EXCLUDED.title,
                    content_hash = EXCLUDED.content_hash,
                    byte_size    = EXCLUDED.byte_size,
                    page_count   = EXCLUDED.page_count,
                    metadata     = EXCLUDED.metadata,
                    ingested_at  = now()
            RETURNING id
        """)
        chunk_sql = sqlalchemy.text("""
            INSERT INTO rag_chunks
                   (document_id, corpus, chunk_index, ref, text, token_count,
                    embedding, embedding_model, metadata)
            VALUES (:document_id, :corpus, :chunk_index, :ref, :text, :token_count,
                    CAST(:embedding AS vector), :embedding_model, CAST(:metadata AS jsonb))
        """)

        with self._engine.begin() as conn:
            document_id = conn.execute(document_sql, {
                "corpus": corpus.value,
                "source_uri": source_uri,
                "title": title,
                "content_hash": content_hash,
                "byte_size": byte_size,
                "page_count": page_count,
                "metadata": json.dumps(metadata),
            }).scalar_one()

            conn.execute(
                sqlalchemy.text("DELETE FROM rag_chunks WHERE document_id = :id"),
                {"id": document_id},
            )

            if chunks:
                conn.execute(chunk_sql, [
                    {
                        "document_id": document_id,
                        "corpus": corpus.value,
                        "chunk_index": chunk.chunk_index,
                        "ref": chunk.ref,
                        "text": chunk.text,
                        "token_count": chunk.token_count,
                        "embedding": to_vector_literal(vector),
                        "embedding_model": embedding_model,
                        "metadata": json.dumps(chunk.metadata),
                    }
                    for chunk, vector in zip(chunks, vectors)
                ])

        log.info("Stored %s (%s): %d chunk(s).", source_uri, corpus.value, len(chunks))
        return document_id

    def delete_document(self, corpus: Corpus, source_uri: str) -> bool:
        """Remove a document and (via ON DELETE CASCADE) all of its chunks."""
        sql = sqlalchemy.text("""
            DELETE FROM rag_documents
             WHERE corpus = :corpus AND source_uri = :source_uri
        """)
        with self._engine.begin() as conn:
            deleted = conn.execute(
                sql, {"corpus": corpus.value, "source_uri": source_uri}
            ).rowcount
        if deleted:
            log.info("Deleted %s from %s.", source_uri, corpus.value)
        return bool(deleted)
