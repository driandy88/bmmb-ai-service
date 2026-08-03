-- RAG vector store — lives in the SHARED `bmmb` database, alongside the
-- extraction service's tables (templates / attributes / template_attributes).
-- Everything here is `rag_`-prefixed so the two never collide.
--
-- THIS IS A TEMPLATE, not directly runnable: the embedding dimension and the
-- full-text config are substituted from settings before execution, because
-- both must be SQL literals (a `vector(N)` column is fixed-width, and a
-- GENERATED column expression must be IMMUTABLE, which rules out reading a
-- config at runtime).
--
-- Apply it with:
--     python scripts/rag_db.py apply            # renders + executes
--     python scripts/rag_db.py render           # print the rendered SQL only
--
-- Re-running is safe: every statement is IF NOT EXISTS.

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per source file. Exists so citations can name a real document and so
-- re-ingesting an unchanged file is a no-op (matched on content_hash).
CREATE TABLE IF NOT EXISTS rag_documents (
    id            BIGSERIAL PRIMARY KEY,
    corpus        TEXT        NOT NULL,
    source_uri    TEXT        NOT NULL,   -- path relative to the corpus root, or a gs:// URI
    title         TEXT,
    content_hash  TEXT        NOT NULL,   -- sha256 of the raw bytes
    byte_size     BIGINT,
    page_count    INTEGER,
    metadata      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (corpus, source_uri)
);

-- One row per retrievable chunk. `ref` is the human-readable citation that
-- surfaces to the end user as RetrievalChunk.ref.
CREATE TABLE IF NOT EXISTS rag_chunks (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT      NOT NULL REFERENCES rag_documents (id) ON DELETE CASCADE,
    corpus          TEXT        NOT NULL,
    chunk_index     INTEGER     NOT NULL,
    ref             TEXT        NOT NULL,   -- e.g. "SME Program T&C — 3.2 Eligibility (p.4)"
    text            TEXT        NOT NULL,
    token_count     INTEGER,
    embedding       vector(${EMBEDDING_DIM}),
    -- Which model produced `embedding`. Retrieval filters on this so vectors
    -- from a different model are never compared against the live query vector.
    embedding_model TEXT,
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('${FTS_CONFIG}', text)) STORED,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

-- Vector arm. HNSW over cosine distance — the operator class must match the
-- distance operator used at query time (`<=>`), or the index is silently skipped.
CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw
    ON rag_chunks USING hnsw (embedding vector_cosine_ops);

-- Lexical arm (phase 5).
CREATE INDEX IF NOT EXISTS rag_chunks_tsv_gin
    ON rag_chunks USING gin (tsv);

-- Both arms filter by corpus first — corpora must never bleed into each other.
CREATE INDEX IF NOT EXISTS rag_chunks_corpus_idx
    ON rag_chunks (corpus);

CREATE INDEX IF NOT EXISTS rag_documents_corpus_idx
    ON rag_documents (corpus);
