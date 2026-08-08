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


@dataclass(frozen=True)
class Settings:
    # ── Environment ──────────────────────────────────────────────────────────
    app_env: str = os.getenv("APP_ENV", "dev")

    # ── Vertex AI / Gemini ───────────────────────────────────────────────────
    gcp_project_id: str | None = os.getenv("GCP_PROJECT_ID") or None
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
    extraction_service_url: str | None = os.getenv("EXTRACTION_SERVICE_URL") or None

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

    # ── Citation source preview (Tier 1) ─────────────────────────────────────
    #   signed | proxy | off  (see integrations/source_preview.py). Off offline/stub.
    source_preview_mode: str = os.getenv(
        "SOURCE_PREVIEW_MODE", "signed" if os.getenv("GCP_PROJECT_ID") else "off"
    ).strip().lower()
    source_url_ttl_seconds: int = int(os.getenv("SOURCE_URL_TTL_SECONDS", "900"))

    # ── API ──────────────────────────────────────────────────────────────────
    allowed_origins: str = os.getenv("ALLOWED_ORIGINS", "*")

    # ── Cloud SQL (checkpointer / audit durability / RAG store) — optional ───
    instance_connection_name: str | None = os.getenv("INSTANCE_CONNECTION_NAME") or None
    db_user: str | None = os.getenv("DB_USER") or None
    db_pass: str | None = os.getenv("DB_PASS") or None
    db_name: str | None = os.getenv("DB_NAME") or None
    db_use_private_ip: bool = field(default_factory=lambda: _flag("DB_USE_PRIVATE_IP", False))
    # Local dev reaches Cloud SQL through the cloud-sql-proxy (127.0.0.1:5433);
    # prod sets DB_HOST to the private IP. Used by PgVectorRetriever.
    db_host: str | None = os.getenv("DB_HOST") or None
    db_port: int = int(os.getenv("DB_PORT", "5432"))

    # ── RAG retrieval (PgVectorRetriever — brief §6 / §6a) ───────────────────
    # Query embedding MUST match the ingested vectors (gemini-embedding-001 @ 1536).
    embedding_model_id: str = os.getenv("EMBEDDING_MODEL_ID", "gemini-embedding-001")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
    # Hybrid + fusion + abstention knobs (config, not hardcoded — §8).
    rag_hybrid_candidates: int = int(os.getenv("RAG_HYBRID_CANDIDATES", "20"))  # per leg, pre-fusion
    rag_rerank_top_n: int = int(os.getenv("RAG_RERANK_TOP_N", "5"))
    rag_rrf_k: int = int(os.getenv("RAG_RRF_K", "60"))
    # Below this best dense cosine, return EMPTY so the agent abstains (§2.5).
    # Tuned to the current corpus: relevant queries score ~0.66–0.73, out-of-corpus
    # ~0.50, so 0.58 separates cleanly. Re-tune against the eval set as it grows.
    rag_relevance_floor: float = float(os.getenv("RAG_RELEVANCE_FLOOR", "0.58"))

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
    if s.source_preview_mode not in ("signed", "proxy", "off"):
        raise RuntimeError(f"SOURCE_PREVIEW_MODE must be 'signed'|'proxy'|'off', got {s.source_preview_mode!r}")
    return s
