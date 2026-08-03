-- rag_chunks — the single physical store for all three corpora (brief §5, §7).
-- The `corpus` column is the retrieval boundary; `access_tier` is the security
-- control enforced in SQL (§11); `expiry_date` keeps lapsed campaign content out.
-- Idempotent: keyed on chunk_id so Stage 7 re-runs upsert rather than duplicate.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_chunks (
  chunk_id        text PRIMARY KEY,
  corpus          text NOT NULL,
  program_code    text,
  section         text,
  doc_id          text NOT NULL,
  doc_title       text,
  source_uri      text,
  version         text NOT NULL,
  effective_date  date,
  expiry_date     date,
  content_type    text NOT NULL DEFAULT 'standard',   -- standard | campaign | indicative
  access_tier     text NOT NULL DEFAULT 'customer',   -- customer | internal
  lang            text,                                -- en | ms
  content         text NOT NULL,                       -- breadcrumb + section body (embedded text)
  content_tsv     tsvector,                            -- keyword half of hybrid search
  embedding       vector(1536) NOT NULL,               -- gemini-embedding-001 @ 1536 (LOCKED)
  needs_review    boolean NOT NULL DEFAULT false,       -- automated verification flagged its source page (§4)
  approved_by     text,                                -- nullable: reserved for formal product-team approval (§2)
  approved_at     timestamptz,                          -- nullable: reserved (see approved_by)
  indexed_at      timestamptz NOT NULL DEFAULT now()
);

-- Existing deployments: add the column in place (no-op once present). The
-- customer-channel retrieval filter uses it exactly like access_tier (§4).
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS needs_review boolean NOT NULL DEFAULT false;

-- Vector index: HNSW/cosine — better recall/latency than IVFFlat and needs no
-- training data present at build time (§7). Tune ef_search at query time.
CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw
  ON rag_chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- Metadata pre-filter (corpus + access tier) applied before search.
CREATE INDEX IF NOT EXISTS rag_chunks_corpus_tier
  ON rag_chunks (corpus, access_tier);

-- Keyword half of hybrid retrieval.
CREATE INDEX IF NOT EXISTS rag_chunks_content_tsv
  ON rag_chunks USING gin (content_tsv);
