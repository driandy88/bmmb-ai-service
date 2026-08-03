"""
Runtime configuration for the Customer Service (`/chat`) agent.

Everything here is read from the environment (Cloud Run `--set-env-vars` in
prod, `.env` locally via python-dotenv) so nothing operational is baked into
code. Secrets (DB password, etc.) come from Secret Manager -> env, never from
source. See `.env.example` for the full list.

Two "backend" switches make the whole service runnable with zero GCP
credentials (deterministic stub paths) and flip to real GCP with one env var
each -- this is what lets the test notebook and unit tests run offline:
  * LLM_BACKEND = vertex | stub   (NLU: classify / extract / phrase)
  * RAG_BACKEND = stub | vertex | pgvector   (retrieval; see rag/corpora.py)
Both default to the stub when GCP_PROJECT_ID is unset, so "no config" == "runs
locally, deterministically".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _optional(name: str) -> str | None:
    """Read an optional env var, treating a comment-only value as unset.

    python-dotenv hands back the inline comment as the value when the value
    itself is empty — `FOO=   # note` parses as `'# note'`, not `''`. That
    turns a deliberately-blank setting into a truthy string and gets caught
    much later as a confusing connection error, so it is normalised here.
    """
    value = os.getenv(name, "").strip()
    if not value or value.startswith("#"):
        return None
    return value


@dataclass(frozen=True)
class Settings:
    # ── Environment ──────────────────────────────────────────────────────────
    app_env: str = os.getenv("APP_ENV", "dev")

    # ── Vertex AI / Gemini ───────────────────────────────────────────────────
    gcp_project_id: str | None = _optional("GCP_PROJECT_ID")
    vertex_location: str = os.getenv("VERTEX_LOCATION", "asia-southeast1")
    model_id: str = os.getenv("MODEL_ID", "gemini-2.5-flash")

    # ── Backend selection (stub vs real) ─────────────────────────────────────
    # Default to the real backend only when a project is configured; otherwise
    # the deterministic stub so the service boots and self-tests with no creds.
    llm_backend: str = os.getenv(
        "LLM_BACKEND", "vertex" if os.getenv("GCP_PROJECT_ID") else "stub"
    ).strip().lower()
    rag_backend: str = os.getenv("RAG_BACKEND", "stub").strip().lower()
    audit_backend: str = os.getenv("AUDIT_BACKEND", "memory").strip().lower()
    # Default "none" = fully STATELESS: the server retains nothing between
    # requests. Memory is 100% client-supplied via `context` (§5.1); the
    # checkpointer is opt-in durability only.
    session_store_backend: str = os.getenv("SESSION_STORE_BACKEND", "none").strip().lower()
    # Document extraction (services/extraction): the chat agent calls it to pull
    # Tier-1 figures from uploaded docs. stub = deterministic canned fields (no
    # network); http = POST multipart to EXTRACTION_SERVICE_URL/extract.
    extraction_backend: str = os.getenv("EXTRACTION_BACKEND", "stub").strip().lower()
    extraction_service_url: str | None = _optional("EXTRACTION_SERVICE_URL")

    # ── Routing / NLU thresholds (Sheet 9.4) ─────────────────────────────────
    # Default 0.7; tune empirically against the Sheet 1.2 example query bank
    # (notebook Part C). Below this the bot must clarify (R8), never guess.
    confidence_threshold: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.7"))

    # ── Short-memory windowing (§5.1) ────────────────────────────────────────
    # The server ALWAYS re-trims client-supplied history to these bounds; never
    # trust the client to keep it short.
    history_max_turns: int = int(os.getenv("HISTORY_MAX_TURNS", "10"))
    history_max_chars: int = int(os.getenv("HISTORY_MAX_CHARS", "6000"))

    # ── Content placeholders (populated by BMMB) ─────────────────────────────
    new_application_url: str = os.getenv("NEW_APPLICATION_URL", "https://apply.muamalat.example/sme/new")
    handoff_hours: str = os.getenv("HANDOFF_HOURS", "Mon–Fri, 9:00am–5:00pm")

    # ── API ──────────────────────────────────────────────────────────────────
    allowed_origins: str = os.getenv("ALLOWED_ORIGINS", "*")

    # ── RAG: ingestion + retrieval (see docs/RAG_PLAN.md) ────────────────────
    # The vector store lives in the SHARED `bmmb` database (same Cloud SQL
    # instance as the extraction service), in `rag_`-prefixed tables. Kept
    # separate from DB_NAME so the checkpointer/audit can point elsewhere.
    rag_db_name: str = os.getenv("RAG_DB_NAME", "bmmb")
    # Escape hatch for a plain Postgres (local docker, CI) — when set, the
    # Cloud SQL connector is bypassed entirely.
    rag_db_url: str | None = _optional("RAG_DB_URL")
    # vertex = Vertex AI embeddings, ADC (deployed default) | hf = Hugging Face
    # Inference API, needs HF_TOKEN | hash = deterministic offline vectors, no
    # credentials, for tests.
    rag_embedding_provider: str = os.getenv(
        "RAG_EMBEDDING_PROVIDER", "vertex" if os.getenv("GCP_PROJECT_ID") else "hash"
    ).strip().lower()
    # Empty = the provider's own default model (see embeddings.py).
    rag_embedding_model: str = _optional("RAG_EMBEDDING_MODEL") or ""
    # Hugging Face access token, required only by RAG_EMBEDDING_PROVIDER=hf.
    # A secret: keep it in .env locally and Secret Manager in prod.
    hf_token: str | None = _optional("HF_TOKEN")
    # MUST match the deployed model's output width AND the `vector(N)` column
    # in schema.sql. 1024 is the shared width both providers emit:
    # multilingual-e5-large is natively 1024, and gemini-embedding-001
    # truncates to match — so the default config works with either, which is
    # the whole point of them being interchangeable.
    # Changing this after ingest requires re-applying the DDL and re-ingesting.
    rag_embedding_dim: int = int(os.getenv("RAG_EMBEDDING_DIM", "1024"))
    # Postgres full-text config for the lexical arm. `simple` (no stemming) is
    # deliberate: the corpus mixes Bahasa Malaysia and English, and the
    # `english` stemmer mangles Malay tokens.
    rag_fts_config: str = os.getenv("RAG_FTS_CONFIG", "simple")
    # Chunking (approximate tokens — see chunker.estimate_tokens).
    rag_chunk_tokens: int = int(os.getenv("RAG_CHUNK_TOKENS", "400"))
    rag_chunk_overlap_tokens: int = int(os.getenv("RAG_CHUNK_OVERLAP_TOKENS", "60"))
    # Chunks below this are folded into a neighbouring chunk of the same
    # section — one-line clauses and cover titles retrieve as noise on their own.
    rag_chunk_min_tokens: int = int(os.getenv("RAG_CHUNK_MIN_TOKENS", "40"))
    # Retrieval widths: fetch `candidate_k` per arm, return `top_k` after fusion.
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "5"))
    rag_candidate_k: int = int(os.getenv("RAG_CANDIDATE_K", "40"))

    # ── Cloud SQL (checkpointer / audit durability) — optional ───────────────
    instance_connection_name: str | None = _optional("INSTANCE_CONNECTION_NAME")
    db_user: str | None = _optional("DB_USER")
    db_pass: str | None = _optional("DB_PASS")
    db_name: str | None = _optional("DB_NAME")
    db_use_private_ip: bool = field(default_factory=lambda: _flag("DB_USE_PRIVATE_IP", False))

    def origins_list(self) -> list[str]:
        if self.allowed_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    if s.app_env not in ("dev", "prod"):
        raise RuntimeError(f"APP_ENV must be 'dev' or 'prod', got {s.app_env!r}")
    if s.llm_backend not in ("vertex", "stub"):
        raise RuntimeError(f"LLM_BACKEND must be 'vertex' or 'stub', got {s.llm_backend!r}")
    if s.rag_backend not in ("stub", "vertex", "pgvector"):
        raise RuntimeError(f"RAG_BACKEND must be 'stub'|'vertex'|'pgvector', got {s.rag_backend!r}")
    if s.extraction_backend not in ("stub", "http"):
        raise RuntimeError(f"EXTRACTION_BACKEND must be 'stub' or 'http', got {s.extraction_backend!r}")
    if s.rag_embedding_provider not in ("vertex", "hf", "hash"):
        raise RuntimeError(
            f"RAG_EMBEDDING_PROVIDER must be 'vertex'|'hf'|'hash', got {s.rag_embedding_provider!r}"
        )
    if s.rag_embedding_dim <= 0:
        raise RuntimeError(f"RAG_EMBEDDING_DIM must be positive, got {s.rag_embedding_dim}")
    if s.rag_chunk_overlap_tokens >= s.rag_chunk_tokens:
        # Overlap >= size means every chunk restarts inside the previous one and
        # the chunker never advances.
        raise RuntimeError(
            f"RAG_CHUNK_OVERLAP_TOKENS ({s.rag_chunk_overlap_tokens}) must be smaller than "
            f"RAG_CHUNK_TOKENS ({s.rag_chunk_tokens})"
        )
    return s
