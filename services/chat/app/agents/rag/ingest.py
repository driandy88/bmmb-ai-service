"""
RAG write-path entry point (brief §11.1 step 6).

The implementation lives in `ingestion/` — one module per stage (loader,
parser, chunker, store, pipeline) so each is separately runnable and testable.
This module is the stable name other code imports.

    from app.agents.rag.ingest import ingest
    report = ingest(Corpus.PROGRAM)

Operationally, `scripts/rag_ingest.py` is the way in: it adds --dry-run,
--force, and --prune, and prints a per-document report.

Note the signature changed when this stopped being a placeholder: it takes a
corpus and reads that corpus's directory, rather than a caller-supplied
`list[dict]` of documents. Discovery and hashing belong to the pipeline, since
that is what makes re-ingestion idempotent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.agents.rag.ingestion.pipeline import IngestionReport, ingest_corpus
from app.agents.rag.retriever import Corpus

__all__ = ["ingest", "IngestionReport"]


def ingest(
    corpus: Corpus,
    *,
    root: Optional[Path] = None,
    force: bool = False,
    dry_run: bool = False,
    prune: bool = False,
) -> IngestionReport:
    """Chunk, embed, and store every document in `corpus`.

    Idempotent: a document whose bytes are unchanged since the last run is
    skipped before it is parsed or embedded. Pass `force=True` to re-embed
    regardless — needed after changing the embedding model or the chunk sizing,
    neither of which changes the source bytes.
    """
    return ingest_corpus(corpus, root=root, force=force, dry_run=dry_run, prune=prune)
