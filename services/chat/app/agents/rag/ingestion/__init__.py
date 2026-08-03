"""
RAG write path (docs/RAG_PLAN.md) — discover → parse → chunk → embed → store.

Split into one module per stage so each is separately runnable and testable:

    loader.py    files on disk -> SourceFile (with content hash)
    parser.py    SourceFile    -> ParsedDoc (blocks carrying page + heading)
    chunker.py   ParsedDoc     -> Chunk[]   (with citation `ref`)
    store.py     Chunk[]       -> Postgres  [phase 3]
    pipeline.py  the whole path, idempotent [phase 3]

Stages 1-3 are pure and need no credentials, so `scripts/rag_ingest.py
--dry-run` shows exactly what would be stored before anything is written.
"""
