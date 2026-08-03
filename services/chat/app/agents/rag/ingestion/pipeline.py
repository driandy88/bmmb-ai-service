"""
The write path, end to end (RAG_PLAN phase 3).

    discover → parse → chunk → embed → store

Everything above this module is a pure stage; this is where the decisions live,
and there is really only one: **should this document be processed at all?**

Re-ingestion is expected to be routine — a corpus gets a new file, one clause
changes, someone re-runs the job to be sure. So an unchanged document must cost
nothing. Each source file's sha256 is compared with the `content_hash` already
stored, and a match short-circuits before parsing and, crucially, before
embedding. Embeddings are the expensive part (a network round trip per batch,
billed per token), so the skip is what makes re-running free rather than merely
idempotent.

Change detection is at **document** granularity: if the bytes differ at all,
the whole document is re-chunked and re-embedded. Finer-grained diffing (per
chunk) is possible but brittle — re-chunking shifts every boundary whenever
`RAG_CHUNK_TOKENS` changes, so it degrades to a full re-embed exactly when you
would most want it not to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Sequence

from app.agents.rag.embeddings import Embedder, get_embedder
from app.agents.rag.ingestion import chunker, loader, parser
from app.agents.rag.ingestion.loader import SourceFile
from app.agents.rag.ingestion.store import ChunkStore, DocumentRecord
from app.agents.rag.retriever import Corpus
from app.utils.logging import get_logger

log = get_logger("rag.pipeline")


class Outcome(str, Enum):
    ADDED = "added"        # new document
    UPDATED = "updated"    # content hash changed
    SKIPPED = "skipped"    # unchanged — nothing parsed, nothing embedded
    PRUNED = "pruned"      # in the database but gone from disk
    FAILED = "failed"


@dataclass
class DocumentOutcome:
    source_uri: str
    outcome: Outcome
    chunks: int = 0
    tokens: int = 0
    error: Optional[str] = None


@dataclass
class IngestionReport:
    corpus: Corpus
    documents: list[DocumentOutcome] = field(default_factory=list)
    dry_run: bool = False

    def count(self, outcome: Outcome) -> int:
        return sum(1 for d in self.documents if d.outcome is outcome)

    @property
    def total_chunks(self) -> int:
        return sum(d.chunks for d in self.documents)

    @property
    def total_tokens(self) -> int:
        return sum(d.tokens for d in self.documents)

    @property
    def failed(self) -> list[DocumentOutcome]:
        return [d for d in self.documents if d.outcome is Outcome.FAILED]

    def summary(self) -> str:
        parts = [f"{self.count(o)} {o.value}" for o in Outcome if self.count(o)]
        return ", ".join(parts) or "nothing to do"


def _stale_reason(
    existing: DocumentRecord, content_hash: str, embedding_model: str,
) -> Optional[str]:
    """Why a stored document needs re-ingesting, or None if it is current.

    Unchanged bytes alone are NOT sufficient to skip. Swapping
    `RAG_EMBEDDING_PROVIDER` leaves the source files untouched while making
    every stored vector incomparable with the queries the retriever will now
    issue — cosine distance between two different models' vectors is noise, and
    it fails silently, degrading results rather than raising. So the embedding
    model is part of the freshness check.
    """
    if existing.content_hash != content_hash:
        return "content hash changed"
    if existing.chunk_count == 0:
        return "no chunks stored (previous run interrupted?)"
    if existing.embedding_model is None:
        return "chunks embedded by more than one model"
    if existing.embedding_model != embedding_model:
        return f"embedded by {existing.embedding_model}, now using {embedding_model}"
    return None


def ingest_sources(
    corpus: Corpus,
    sources: Sequence[SourceFile],
    *,
    embedder: Optional[Embedder] = None,
    store: Optional[ChunkStore] = None,
    force: bool = False,
    dry_run: bool = False,
) -> IngestionReport:
    """Run the write path over already-discovered files.

    `dry_run` stops after chunking — nothing is embedded and nothing is
    written, so it costs no API calls and needs no database.
    """
    report = IngestionReport(corpus=corpus, dry_run=dry_run)

    # Built lazily so --dry-run works with neither credentials nor a database.
    if not dry_run:
        embedder = embedder or get_embedder()
        store = store or ChunkStore()

    for source in sources:
        try:
            # The lookup runs even under --force: it costs one indexed query
            # and is what makes the report say "updated" rather than "added"
            # for a document that was already there. Only the *comparisons*
            # below are what --force skips.
            existing = None if dry_run else store.find_document(corpus, source.source_uri)
            if existing and not force:
                reason = _stale_reason(existing, source.content_hash, embedder.model)
                if reason is None:
                    log.info("Unchanged, skipping: %s", source.source_uri)
                    report.documents.append(
                        DocumentOutcome(source.source_uri, Outcome.SKIPPED)
                    )
                    continue
                log.info("Re-ingesting %s: %s", source.source_uri, reason)
            was_present = existing is not None

            document = parser.parse(source)
            chunks = chunker.chunk_document(document)
            tokens = sum(chunk.token_count for chunk in chunks)

            if dry_run:
                report.documents.append(DocumentOutcome(
                    source.source_uri, Outcome.ADDED, len(chunks), tokens,
                ))
                continue

            vectors = embedder.embed_documents([chunk.text for chunk in chunks])
            store.write_document(
                corpus=corpus,
                source_uri=source.source_uri,
                title=document.title,
                content_hash=source.content_hash,
                byte_size=source.byte_size,
                page_count=document.page_count,
                metadata={**document.metadata, "suffix": source.suffix},
                chunks=chunks,
                vectors=vectors,
                embedding_model=embedder.model,
            )
            report.documents.append(DocumentOutcome(
                source.source_uri,
                Outcome.UPDATED if was_present else Outcome.ADDED,
                len(chunks), tokens,
            ))

        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the run
            log.error("Failed to ingest %s: %s", source.source_uri, exc)
            report.documents.append(
                DocumentOutcome(source.source_uri, Outcome.FAILED, error=str(exc))
            )

    return report


def ingest_corpus(
    corpus: Corpus,
    *,
    root: Optional[Path] = None,
    embedder: Optional[Embedder] = None,
    store: Optional[ChunkStore] = None,
    force: bool = False,
    dry_run: bool = False,
    prune: bool = False,
) -> IngestionReport:
    """Ingest every supported document in a corpus directory.

    `prune` removes rows for documents that no longer exist on disk. Off by
    default and never implied by a normal run: a file missing because someone
    is mid-edit should not silently delete its chunks.
    """
    sources = loader.discover(corpus, root)
    report = ingest_sources(
        corpus, sources, embedder=embedder, store=store, force=force, dry_run=dry_run,
    )

    if prune and not dry_run:
        store = store or ChunkStore()
        on_disk = {source.source_uri for source in sources}
        for record in store.list_documents(corpus):
            if record.source_uri not in on_disk:
                store.delete_document(corpus, record.source_uri)
                report.documents.append(
                    DocumentOutcome(record.source_uri, Outcome.PRUNED)
                )

    log.info("Corpus %s: %s", corpus.value, report.summary())
    return report
