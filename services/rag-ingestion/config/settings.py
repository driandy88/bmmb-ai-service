"""
Configuration for the rag-ingestion pipeline.

Mirrors the chat service convention: a frozen dataclass read entirely from the
environment (``.env`` locally via python-dotenv, Cloud Run ``--set-env-vars`` in
prod; secrets come from Secret Manager -> env, never from source). Nothing
operational — no model id, threshold, dimension, or corpus name — is hardcoded
elsewhere; this file and the YAML in ``config/`` are the single source of truth
(brief §11: "Do not hardcode thresholds, model ids, corpus names, or prompts").

Kept import-light (stdlib only) so ``cli.py --help`` and per-stage arg parsing
work before any GCP dependency is installed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    # ── Environment ──────────────────────────────────────────────────────────
    app_env: str = os.getenv("APP_ENV", "dev")

    # ── Vertex AI / Gemini (in-region only; §2.7) ────────────────────────────
    gcp_project_id: str | None = os.getenv("GCP_PROJECT_ID") or None
    vertex_location: str = os.getenv("VERTEX_LOCATION", "asia-southeast1")
    # Stage-1 vision transcription model. Confirm the current id at build time
    # (§12) — do not assume a version that has been superseded.
    vision_model_id: str = os.getenv("VISION_MODEL_ID", "gemini-2.5-pro")
    # Text embedding model — LOCKED (§3). Changing the model OR the dimension is a
    # full re-embed and a versioned migration; never mix vector spaces (§11).
    embedding_model_id: str = os.getenv("EMBEDDING_MODEL_ID", "gemini-embedding-001")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
    # Query-rewrite + RAGAS judge model. The judge must be STRONGER than the
    # answer generator to avoid self-preference bias (§8 / design §10.4).
    judge_model_id: str = os.getenv("JUDGE_MODEL_ID", "gemini-2.5-pro")

    # ── Cloud SQL (pgvector) — the store ─────────────────────────────────────
    db_host: str | None = os.getenv("DB_HOST") or None
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "bmmb_rag")
    db_user: str | None = os.getenv("DB_USER") or None
    db_password: str | None = os.getenv("DB_PASSWORD") or None
    # In prod, connect via the Cloud SQL Python Connector using this instance
    # connection name instead of a raw host (project:region:instance).
    cloud_sql_instance: str | None = os.getenv("CLOUD_SQL_INSTANCE") or None

    # ── Cloud Storage — source documents ─────────────────────────────────────
    gcs_sources_bucket: str = os.getenv("GCS_SOURCES_BUCKET", "bmmb-rag-sources")

    # ── Stage 1 · parsing ────────────────────────────────────────────────────
    parse_dpi: int = int(os.getenv("PARSE_DPI", "150"))

    # ── Stage 4 · chunking (§5 / §11: tables never split) ────────────────────
    chunk_target_min_tokens: int = int(os.getenv("CHUNK_TARGET_MIN_TOKENS", "300"))
    chunk_target_max_tokens: int = int(os.getenv("CHUNK_TARGET_MAX_TOKENS", "800"))
    chunk_ceiling_tokens: int = int(os.getenv("CHUNK_CEILING_TOKENS", "1000"))
    chunk_merge_below_tokens: int = int(os.getenv("CHUNK_MERGE_BELOW_TOKENS", "50"))

    # ── Retrieval (Phase 5; consumed by the chat-side PgVectorRetriever) ──────
    retrieval_top_k: int = int(os.getenv("RETRIEVAL_TOP_K", "20"))   # per method, pre-fusion
    rerank_top_n: int = int(os.getenv("RERANK_TOP_N", "5"))
    # Below this fused score the retriever returns EMPTY so the agent abstains
    # honestly and offers a Sales handoff (§2.5). Tune against the eval set.
    relevance_floor: float = float(os.getenv("RELEVANCE_FLOOR", "0.5"))
    hnsw_ef_search: int = int(os.getenv("HNSW_EF_SEARCH", "40"))

    # ── Paths ────────────────────────────────────────────────────────────────
    # Root for the inspectable per-stage artifacts (00_raw … 05_enriched).
    data_dir: str = os.getenv("RAG_DATA_DIR", "data")


# Google recommends truncating the Matryoshka embedding to one of these sizes.
_VALID_DIMS = (256, 768, 1536, 3072)


def validate(s: Settings) -> Settings:
    if s.embedding_dimensions not in _VALID_DIMS:
        raise RuntimeError(
            f"EMBEDDING_DIMENSIONS must be one of {_VALID_DIMS}, got {s.embedding_dimensions}"
        )
    if s.parse_dpi <= 0:
        raise RuntimeError(f"PARSE_DPI must be positive, got {s.parse_dpi}")
    if not (0 < s.chunk_target_min_tokens <= s.chunk_target_max_tokens <= s.chunk_ceiling_tokens):
        raise RuntimeError(
            "chunk token bounds must satisfy 0 < min <= max <= ceiling; got "
            f"{s.chunk_target_min_tokens}/{s.chunk_target_max_tokens}/{s.chunk_ceiling_tokens}"
        )
    if s.rerank_top_n > s.retrieval_top_k:
        raise RuntimeError("RERANK_TOP_N cannot exceed RETRIEVAL_TOP_K")
    return s


@lru_cache
def get_settings() -> Settings:
    return validate(Settings())
