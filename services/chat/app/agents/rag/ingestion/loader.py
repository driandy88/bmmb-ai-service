"""
Stage 1 — find the source documents and identify them (RAG_PLAN phase 1).

Reads nothing but bytes: no parsing happens here. The one job that matters
beyond listing files is the **content hash**, which is what makes re-ingestion
idempotent — an unchanged file is skipped at stage 5 instead of producing a
duplicate set of chunks.

Corpus layout on disk:

    app/agents/rag/corpus/<corpus value>/**/<file>

so `Corpus.PROGRAM` -> `corpus/program/`. A corpus with no directory is not an
error (Sheet 4 was empty for a long time); it yields zero documents.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.agents.rag.retriever import Corpus
from app.utils.logging import get_logger

log = get_logger("rag.loader")

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"

# Extensions the parser layer knows how to read. Anything else is skipped with
# a log line rather than silently ignored — a document that never made it into
# the index is the kind of thing you want to hear about.
SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".pdf", ".docx"})


@dataclass(frozen=True)
class SourceFile:
    corpus: Corpus
    path: Path
    source_uri: str      # path relative to the corpus dir — stable across machines
    suffix: str
    byte_size: int
    content_hash: str    # sha256 hex of the raw bytes

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


def corpus_dir(corpus: Corpus, root: Optional[Path] = None) -> Path:
    return (root or CORPUS_ROOT) / corpus.value


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_file(path: Path, corpus: Corpus, root: Optional[Path] = None) -> SourceFile:
    """Build a SourceFile for one path (used directly by the CLI's file mode)."""
    data = path.read_bytes()
    base = corpus_dir(corpus, root)
    try:
        source_uri = str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        # Outside the corpus tree (ad-hoc file passed on the command line).
        source_uri = path.name
    return SourceFile(
        corpus=corpus,
        path=path,
        source_uri=source_uri,
        suffix=path.suffix.lower(),
        byte_size=len(data),
        content_hash=hash_bytes(data),
    )


def discover(corpus: Corpus, root: Optional[Path] = None) -> list[SourceFile]:
    """Return every supported document in a corpus, sorted for stable ordering."""
    base = corpus_dir(corpus, root)
    if not base.is_dir():
        log.warning("Corpus %s has no directory at %s — 0 documents.", corpus.value, base)
        return []

    found: list[SourceFile] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            log.warning("Skipping unsupported file %s (suffix %r).", path.name, path.suffix)
            continue
        found.append(load_file(path, corpus, root))

    log.info("Corpus %s: %d document(s) in %s", corpus.value, len(found), base)
    return found
