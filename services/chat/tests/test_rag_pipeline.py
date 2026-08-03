"""
Tests for the ingestion pipeline and the store's SQL-facing helpers
(RAG_PLAN phase 3).

Offline: the store is faked, so the add/update/skip decision logic — the part
that decides whether to spend money on embeddings — is covered without a
database. The real `ChunkStore` SQL is exercised against `bmmb` through
`scripts/rag_ingest.py`; it is deliberately not unit-tested here, because
pointing the suite at the shared database would write rows into the live
`program` corpus.
"""
from __future__ import annotations

import pytest

from app.agents.rag.embeddings import HashEmbedder
from app.agents.rag.ingestion import loader, pipeline
from app.agents.rag.ingestion.pipeline import Outcome
from app.agents.rag.ingestion.store import DocumentRecord, to_vector_literal
from app.agents.rag.retriever import Corpus
from app.config.settings import Settings

MARKDOWN = "# Handbook\n\n## 1. Scope\n\nThe rules that apply to SME financing.\n"


@pytest.fixture
def embedder():
    return HashEmbedder(Settings(rag_embedding_dim=16, rag_embedding_model="test-model"))


class FakeStore:
    """In-memory stand-in for ChunkStore, recording what it was asked to do."""

    def __init__(self):
        self.documents: dict[tuple[str, str], dict] = {}
        self.writes: list[dict] = []
        self.deletes: list[str] = []

    @staticmethod
    def _record(uri, stored) -> DocumentRecord:
        return DocumentRecord(
            id=stored["id"], source_uri=uri, content_hash=stored["content_hash"],
            embedding_model=stored["embedding_model"], chunk_count=stored["chunk_count"],
        )

    def find_document(self, corpus, source_uri):
        stored = self.documents.get((corpus.value, source_uri))
        return self._record(source_uri, stored) if stored else None

    def list_documents(self, corpus):
        return [
            self._record(uri, stored)
            for (c, uri), stored in self.documents.items() if c == corpus.value
        ]

    def write_document(self, **kwargs):
        key = (kwargs["corpus"].value, kwargs["source_uri"])
        self.documents[key] = {
            "id": len(self.documents) + 1,
            "content_hash": kwargs["content_hash"],
            "embedding_model": kwargs["embedding_model"],
            "chunk_count": len(kwargs["chunks"]),
        }
        self.writes.append(kwargs)
        return self.documents[key]["id"]

    def delete_document(self, corpus, source_uri):
        self.deletes.append(source_uri)
        return bool(self.documents.pop((corpus.value, source_uri), None))

    def stats(self, corpus):
        return {"documents": len(self.documents), "chunks": 0, "embedded": 0}


@pytest.fixture
def corpus_dir(tmp_path):
    directory = tmp_path / "program"
    directory.mkdir(parents=True)
    (directory / "handbook.md").write_text(MARKDOWN, encoding="utf-8")
    return tmp_path


def _run(corpus_dir, store, embedder, **kwargs):
    return pipeline.ingest_corpus(
        Corpus.PROGRAM, root=corpus_dir, store=store, embedder=embedder, **kwargs,
    )


# ── first ingest ─────────────────────────────────────────────────────────────

def test_new_document_is_added_and_written(corpus_dir, embedder):
    store = FakeStore()
    report = _run(corpus_dir, store, embedder)

    assert report.count(Outcome.ADDED) == 1
    assert report.total_chunks > 0
    assert len(store.writes) == 1

    written = store.writes[0]
    assert written["source_uri"] == "handbook.md"
    assert written["title"] == "Handbook"
    assert written["embedding_model"] == "test-model"
    assert len(written["chunks"]) == len(written["vectors"])
    assert all(len(v) == 16 for v in written["vectors"])


# ── the skip mechanism (the point of phase 3) ────────────────────────────────

def test_unchanged_document_is_skipped_on_re_run(corpus_dir, embedder):
    store = FakeStore()
    _run(corpus_dir, store, embedder)
    report = _run(corpus_dir, store, embedder)

    assert report.count(Outcome.SKIPPED) == 1
    assert report.count(Outcome.ADDED) == 0
    assert len(store.writes) == 1          # no second write


def test_skipping_does_not_embed(corpus_dir, monkeypatch):
    # The whole point: embeddings are the billed, slow part, so an unchanged
    # document must not reach the embedder at all.
    class CountingEmbedder(HashEmbedder):
        calls = 0

        def embed_documents(self, texts):
            CountingEmbedder.calls += 1
            return super().embed_documents(texts)

    counting = CountingEmbedder(Settings(rag_embedding_dim=16))
    store = FakeStore()
    _run(corpus_dir, store, counting)
    assert CountingEmbedder.calls == 1

    _run(corpus_dir, store, counting)
    assert CountingEmbedder.calls == 1      # unchanged — no second embed


def test_swapping_the_embedding_model_forces_a_re_embed(corpus_dir):
    # The source bytes are identical, so a hash-only check would skip — leaving
    # vectors from the old model that are not comparable with queries embedded
    # by the new one. That failure is silent, so it has to be caught here.
    store = FakeStore()
    _run(corpus_dir, store, HashEmbedder(
        Settings(rag_embedding_dim=16, rag_embedding_model="model-a")))

    report = _run(corpus_dir, store, HashEmbedder(
        Settings(rag_embedding_dim=16, rag_embedding_model="model-b")))

    assert report.count(Outcome.SKIPPED) == 0
    assert report.count(Outcome.UPDATED) == 1
    assert store.writes[-1]["embedding_model"] == "model-b"


def test_same_model_and_unchanged_bytes_still_skips(corpus_dir, embedder):
    # The complement of the test above: the model check must not defeat the
    # skip when nothing actually changed.
    store = FakeStore()
    _run(corpus_dir, store, embedder)
    assert _run(corpus_dir, store, embedder).count(Outcome.SKIPPED) == 1


def test_a_document_with_no_stored_chunks_is_re_ingested(corpus_dir, embedder):
    # An interrupted previous run can leave the document row without chunks;
    # the hash would match and the document would be skipped forever.
    store = FakeStore()
    _run(corpus_dir, store, embedder)
    store.documents[("program", "handbook.md")]["chunk_count"] = 0

    assert _run(corpus_dir, store, embedder).count(Outcome.UPDATED) == 1


def test_chunks_embedded_by_mixed_models_are_re_ingested(corpus_dir, embedder):
    # None means the stored chunks disagree about their model — untrustworthy,
    # so re-embed rather than assume.
    store = FakeStore()
    _run(corpus_dir, store, embedder)
    store.documents[("program", "handbook.md")]["embedding_model"] = None

    assert _run(corpus_dir, store, embedder).count(Outcome.UPDATED) == 1


def test_changed_document_is_re_ingested_as_updated(corpus_dir, embedder):
    store = FakeStore()
    _run(corpus_dir, store, embedder)

    (corpus_dir / "program" / "handbook.md").write_text(
        MARKDOWN + "\n## 2. Fees\n\nA processing fee applies.\n", encoding="utf-8",
    )
    report = _run(corpus_dir, store, embedder)

    assert report.count(Outcome.UPDATED) == 1
    assert len(store.writes) == 2
    assert store.writes[1]["content_hash"] != store.writes[0]["content_hash"]


def test_force_re_ingests_an_unchanged_document(corpus_dir, embedder):
    # Needed after changing the embedding model or chunk sizing, neither of
    # which alters the source bytes.
    store = FakeStore()
    _run(corpus_dir, store, embedder)
    report = _run(corpus_dir, store, embedder, force=True)

    assert report.count(Outcome.SKIPPED) == 0
    assert len(store.writes) == 2


def test_force_still_reports_an_existing_document_as_updated(corpus_dir, embedder):
    # --force skips the hash comparison, not the existence check — otherwise
    # every forced re-run would claim to have added documents it replaced.
    store = FakeStore()
    _run(corpus_dir, store, embedder)
    report = _run(corpus_dir, store, embedder, force=True)

    assert report.count(Outcome.UPDATED) == 1
    assert report.count(Outcome.ADDED) == 0


# ── dry run ──────────────────────────────────────────────────────────────────

def test_dry_run_writes_nothing_and_needs_no_store(corpus_dir):
    report = pipeline.ingest_corpus(Corpus.PROGRAM, root=corpus_dir, dry_run=True)

    assert report.dry_run
    assert report.count(Outcome.ADDED) == 1
    assert report.total_chunks > 0


def test_dry_run_does_not_embed(corpus_dir):
    class ExplodingEmbedder(HashEmbedder):
        def embed_documents(self, texts):
            raise AssertionError("dry run must not embed")

    report = pipeline.ingest_corpus(
        Corpus.PROGRAM, root=corpus_dir,
        embedder=ExplodingEmbedder(Settings(rag_embedding_dim=16)), dry_run=True,
    )
    assert report.count(Outcome.FAILED) == 0


# ── pruning ──────────────────────────────────────────────────────────────────

def test_prune_removes_documents_that_left_the_disk(corpus_dir, embedder):
    store = FakeStore()
    _run(corpus_dir, store, embedder)
    (corpus_dir / "program" / "handbook.md").unlink()

    report = _run(corpus_dir, store, embedder, prune=True)

    assert report.count(Outcome.PRUNED) == 1
    assert store.deletes == ["handbook.md"]


def test_missing_files_are_left_alone_without_prune(corpus_dir, embedder):
    # A file missing mid-edit must not silently delete its chunks.
    store = FakeStore()
    _run(corpus_dir, store, embedder)
    (corpus_dir / "program" / "handbook.md").unlink()

    report = _run(corpus_dir, store, embedder)

    assert report.count(Outcome.PRUNED) == 0
    assert store.deletes == []


# ── failure isolation ────────────────────────────────────────────────────────

def test_one_bad_document_does_not_stop_the_others(corpus_dir, embedder):
    (corpus_dir / "program" / "broken.pdf").write_bytes(b"not really a pdf")
    store = FakeStore()

    report = _run(corpus_dir, store, embedder)

    assert report.count(Outcome.FAILED) == 1
    assert report.count(Outcome.ADDED) == 1        # the good one still landed
    assert report.failed[0].source_uri == "broken.pdf"
    assert report.failed[0].error


def test_report_summary_counts_mixed_outcomes(corpus_dir, embedder):
    store = FakeStore()
    _run(corpus_dir, store, embedder)                       # handbook.md -> added

    # Now one unchanged document, one new one, and one that cannot be parsed.
    (corpus_dir / "program" / "second.md").write_text("# Second\n\nMore text.\n",
                                                      encoding="utf-8")
    (corpus_dir / "program" / "broken.pdf").write_bytes(b"not really a pdf")
    summary = _run(corpus_dir, store, embedder).summary()

    assert "1 added" in summary
    assert "1 skipped" in summary
    assert "1 failed" in summary


def test_report_summary_of_an_empty_corpus(tmp_path):
    (tmp_path / "program").mkdir(parents=True)
    assert pipeline.ingest_corpus(
        Corpus.PROGRAM, root=tmp_path, dry_run=True,
    ).summary() == "nothing to do"


# ── store helpers ────────────────────────────────────────────────────────────

def test_vector_literal_matches_pgvector_input_format():
    assert to_vector_literal([0.5, -0.25, 1.0]) == "[0.5,-0.25,1.0]"


def test_vector_literal_handles_an_empty_vector():
    assert to_vector_literal([]) == "[]"


def test_vector_literal_round_trips_full_precision():
    # Truncating here would quietly change every similarity score.
    value = 0.123456789012345
    assert str(value) in to_vector_literal([value])


def test_write_document_rejects_mismatched_chunks_and_vectors(embedder):
    from app.agents.rag.ingestion.store import ChunkStore

    store = ChunkStore(engine=object())     # never reached — validation is first
    with pytest.raises(ValueError, match="2 chunks but 1 vectors"):
        store.write_document(
            corpus=Corpus.PROGRAM, source_uri="a.md", title="A", content_hash="h",
            byte_size=1, page_count=None, metadata={},
            chunks=[object(), object()], vectors=[[0.1]], embedding_model="m",
        )


def test_loader_and_pipeline_agree_on_the_hash(corpus_dir, embedder):
    # The skip decision compares these two; if they ever disagree, every run
    # would look like a change.
    store = FakeStore()
    _run(corpus_dir, store, embedder)

    source = loader.discover(Corpus.PROGRAM, corpus_dir)[0]
    assert store.writes[0]["content_hash"] == source.content_hash
