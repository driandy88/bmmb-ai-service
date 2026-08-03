"""
The RAG store's connection layer — the ONE place that knows how to reach the
vector database (docs/RAG_PLAN.md phase 0).

Both RAG modules go through here: ingestion (write path) and retrieval (read
path). Neither builds its own engine, so credentials, pooling, and the target
database are decided in exactly one file.

Connects via the Cloud SQL Python Connector rather than host:port, mirroring
`services/extraction/app/config.py`, so the same code path works locally (ADC
via `gcloud auth application-default login`) and on Cloud Run (attached service
account) with no proxy sidecar. `RAG_DB_URL` bypasses the connector entirely
for a plain Postgres (local docker / CI).

Target database is `RAG_DB_NAME` (default `bmmb`) — the shared database, NOT
`bmmb_dev`. See schema.sql for the tables.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import sqlalchemy

from app.config.settings import Settings, get_settings
from app.utils.logging import get_logger

log = get_logger("rag.db")

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Module-level so one process reuses one pool. Keyed by nothing: a process
# talks to exactly one RAG database.
_connector = None
_engine: Optional[sqlalchemy.engine.Engine] = None


class RagDbNotConfigured(RuntimeError):
    """Raised when the RAG store is used without connection settings.

    Deliberately explicit rather than falling back to something empty — a
    silently unreachable vector store looks exactly like an empty corpus.
    """


def get_engine(settings: Optional[Settings] = None) -> sqlalchemy.engine.Engine:
    """Return the process-wide SQLAlchemy engine for the RAG database."""
    global _connector, _engine
    if _engine is not None:
        return _engine

    settings = settings or get_settings()

    if settings.rag_db_url:
        log.info("RAG store: connecting via RAG_DB_URL (Cloud SQL connector bypassed).")
        _engine = sqlalchemy.create_engine(settings.rag_db_url, pool_pre_ping=True)
        return _engine

    missing = [
        name
        for name, value in (
            ("INSTANCE_CONNECTION_NAME", settings.instance_connection_name),
            ("DB_USER", settings.db_user),
            ("DB_PASS", settings.db_pass),
        )
        if not value
    ]
    if missing:
        raise RagDbNotConfigured(
            "RAG store needs " + ", ".join(missing) + " (or RAG_DB_URL). "
            "See .env.example — the Cloud SQL instance is "
            "prototype-bmmb-1b62:asia-southeast1:bmmb."
        )

    from google.cloud.sql.connector import Connector, IPTypes

    _connector = Connector()

    def _getconn():
        return _connector.connect(
            settings.instance_connection_name,
            "pg8000",
            user=settings.db_user,
            password=settings.db_pass,
            db=settings.rag_db_name,
            ip_type=IPTypes.PRIVATE if settings.db_use_private_ip else IPTypes.PUBLIC,
        )

    # Small pool: retrieval issues a couple of queries per turn, and ingestion
    # is a single-threaded batch job.
    _engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=_getconn,
        pool_size=5,
        max_overflow=2,
        pool_timeout=30,
        pool_recycle=1800,
    )
    log.info("RAG store: Cloud SQL %s db=%s",
             settings.instance_connection_name, settings.rag_db_name)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine/connector. For tests and CLI re-config."""
    global _connector, _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
    if _connector is not None:
        _connector.close()
    _connector = None


def split_statements(sql: str) -> list[str]:
    """Split a DDL script into individually executable statements.

    pg8000 sends one statement per execute, so the script cannot be handed over
    whole. Comments are stripped as part of the walk rather than by a separate
    pass, because the two interact: an apostrophe inside a comment (`the
    service's tables`) looks exactly like the start of a string literal, and
    treating it as one swallows every following semicolon — the whole file then
    collapses into a single statement.
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    index, length = 0, len(sql)

    while index < length:
        char = sql[index]

        if in_string:
            # '' is an escaped quote, not the end of the literal.
            if char == "'" and index + 1 < length and sql[index + 1] == "'":
                current.append("''")
                index += 2
                continue
            if char == "'":
                in_string = False
            current.append(char)
            index += 1
            continue

        if char == "'":
            in_string = True
            current.append(char)
            index += 1
            continue

        if char == "-" and index + 1 < length and sql[index + 1] == "-":
            while index < length and sql[index] != "\n":
                index += 1
            continue

        if char == ";":
            statements.append("".join(current))
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip()]


def render_schema(settings: Optional[Settings] = None) -> str:
    """Render schema.sql with the embedding dimension and FTS config applied.

    Both are substituted rather than parameterised because Postgres requires
    them as literals (fixed-width vector column; IMMUTABLE generated column).
    """
    settings = settings or get_settings()
    fts = settings.rag_fts_config
    # This value is interpolated straight into DDL, so constrain it to an
    # identifier rather than trusting the environment.
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", fts):
        raise RuntimeError(f"RAG_FTS_CONFIG must be a plain identifier, got {fts!r}")
    return (
        _SCHEMA_PATH.read_text(encoding="utf-8")
        .replace("${EMBEDDING_DIM}", str(int(settings.rag_embedding_dim)))
        .replace("${FTS_CONFIG}", fts)
    )
