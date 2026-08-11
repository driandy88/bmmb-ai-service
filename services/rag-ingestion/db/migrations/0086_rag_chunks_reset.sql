-- 0086_rag_chunks_reset
-- ---------------------------------------------------------------------------
-- Wipe the RAG vector store (rag_chunks) so the corpus can be rebuilt from a
-- SINGLE new source document, replacing everything ingested before (the 6 docs
-- ggsm3 / mhp_i / mihp_i / sjum / proud / commercial_financing_internal_criteria,
-- 20 chunks total at time of writing).
--
-- Follows the numbered `schema_migrations` convention this database already uses
-- (0001..0085 on bmmb_dev / bmmb_prod): this file is 0086, run BY HAND against
-- the target DB and recorded in schema_migrations at the end — exactly like the
-- platform's own out-of-band scripts (see bmmb-sme-financing-platform/backend/
-- scripts/migrate_ids_to_uuid.sql). rag_chunks itself is owned by the
-- rag-ingestion service (services/rag-ingestion/db/schema.sql); this migration
-- (re)asserts that schema idempotently, then TRUNCATEs it — so it is safe on a
-- fresh DB (creates + empties) and on the live one (just empties).
--
-- This is a DATA reset, not a schema change: the embedding model / dimension is
-- unchanged (gemini-embedding-001 @ 1536, LOCKED), so nothing downstream in the
-- retriever's embedding contract shifts.
--
-- Dev only (per request). Run once against bmmb_dev, verify (bottom SELECT),
-- THEN run the ingestion pipeline to repopulate from the new document. Through
-- the Cloud SQL Auth Proxy:
--   cloud-sql-proxy prototype-bmmb-1b62:asia-southeast1:bmmb --port 5433
--   # with psql:
--   psql "postgresql://postgres:PASSWORD@127.0.0.1:5433/bmmb_dev" -f 0086_rag_chunks_reset.sql
--   # no psql? apply with the rag-ingestion venv + psycopg:
--   DSN="host=127.0.0.1 port=5433 dbname=bmmb_dev user=postgres password=PASSWORD" \
--     python -c "import os,psycopg,sys; psycopg.connect(os.environ['DSN'],autocommit=True).execute(open(sys.argv[1]).read())" \
--     0086_rag_chunks_reset.sql
--
-- pgvector: CREATE EXTENSION vector is idempotent; TRUNCATE reclaims the rows and
-- leaves the HNSW / btree / GIN indexes in place for the reload.

BEGIN;

-- 1) (Re)assert the rag_chunks schema — idempotent, mirrors
--    services/rag-ingestion/db/schema.sql so this migration is self-contained.
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
  needs_review    boolean NOT NULL DEFAULT false,       -- verification flagged its source page
  approved_by     text,                                -- reserved for product-team approval
  approved_at     timestamptz,                          -- reserved (see approved_by)
  indexed_at      timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS needs_review boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw
  ON rag_chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS rag_chunks_corpus_tier
  ON rag_chunks (corpus, access_tier);
CREATE INDEX IF NOT EXISTS rag_chunks_content_tsv
  ON rag_chunks USING gin (content_tsv);

-- 2) Wipe every existing chunk — the corpus is rebuilt from the new document by
--    the ingestion pipeline after this runs.
TRUNCATE TABLE rag_chunks;

-- 3) Record this migration in the ledger, matching 0001..0085.
INSERT INTO schema_migrations (version, applied_at)
VALUES ('0086_rag_chunks_reset', now())
ON CONFLICT (version) DO NOTHING;

COMMIT;

-- ── verify ───────────────────────────────────────────────────────────────────
-- Expect rag_chunks_rows = 0 and a non-null migration_applied_at.
SELECT (SELECT count(*) FROM rag_chunks)                        AS rag_chunks_rows,
       (SELECT applied_at FROM schema_migrations
        WHERE version = '0086_rag_chunks_reset')                AS migration_applied_at;
