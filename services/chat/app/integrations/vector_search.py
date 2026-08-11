"""
Real retriever backends (brief §6). `PgVectorRetriever` is the shipped one —
Cloud SQL pgvector is the LOCKED store (§3). Both subclass the frozen `Retriever`
interface; selected via RAG_BACKEND (rag/corpora.py). Default stays RAG_BACKEND=stub.

PgVectorRetriever query pipeline (§6):
  1. embed the query — gemini-embedding-001, task RETRIEVAL_QUERY, L2-normalised
     (must match the ingested RETRIEVAL_DOCUMENT vectors).
  2. filter in SQL (mandatory): corpus ∈ scope · not expired · access_tier='customer'
     on the customer channel (§11) · optional program_code pin (§6a).
  3. hybrid: dense (cosine, top-N) AND keyword (content_tsv, top-N).
  4. fuse with Reciprocal Rank Fusion (no score-weight tuning).
  5. relevance floor: if nothing is semantically close, return [] → the agent
     abstains honestly and offers a Sales handoff (§2.5).
  6. return list[RetrievalChunk] {text, corpus, ref, score, metadata} so citations
     render and audit logs get provenance.

Chunk text is DATA, not instructions (§2.3) — the generation prompt delimits it.
"""
from __future__ import annotations

import math
from functools import lru_cache

from ..agents.rag.retriever import Corpus, CorpusScope, RetrievalChunk, Retriever
from ..config.settings import Settings
from ..utils.logging import get_logger

log = get_logger("rag.pgvector")

_QUERY_TASK = "RETRIEVAL_QUERY"

# Both legs also compute cosine so every fused row carries a semantic score (for
# the relevance floor and for citation/audit), not just a fusion rank.
_COLS = ("chunk_id, corpus, program_code, section, doc_id, doc_title, source_uri, "
         "content, content_type, access_tier, version")

_DENSE_SQL = f"""
SELECT {_COLS}, 1 - (embedding <=> %(qv)s::vector) AS cosine
FROM rag_chunks
WHERE {{filters}}
ORDER BY embedding <=> %(qv)s::vector
LIMIT %(cand)s
"""

_KEYWORD_SQL = f"""
SELECT {_COLS}, 1 - (embedding <=> %(qv)s::vector) AS cosine
FROM rag_chunks
WHERE {{filters}} AND content_tsv @@ plainto_tsquery('english', %(q)s)
ORDER BY ts_rank(content_tsv, plainto_tsquery('english', %(q)s)) DESC
LIMIT %(cand)s
"""


@lru_cache(maxsize=1)
def _genai_client(project: str, location: str):
    from google import genai
    return genai.Client(vertexai=True, project=project, location=location)


def _vec_literal(v: list[float]) -> str:
    return "[" + ",".join(map(str, v)) + "]"


class PgVectorRetriever(Retriever):
    def __init__(self, settings: Settings, namespaces: dict[Corpus, str]):
        self._s = settings
        self._namespaces = namespaces  # kept for interface parity; the `corpus` column is the boundary
        self._programs_cache: list[tuple[str, str]] | None = None

    def programs(self) -> list[tuple[str, str]]:
        """Distinct (program_code, doc_title) present in the index — the valid scope
        set for §6a program extraction (learned from the DB, so it matches the index's
        codes, not the chat product config). Cached per instance."""
        if self._programs_cache is not None:
            return self._programs_cache
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT program_code, MIN(doc_title) FROM rag_chunks "
                            "WHERE program_code IS NOT NULL GROUP BY program_code ORDER BY program_code")
                self._programs_cache = [(r[0], r[1]) for r in cur.fetchall()]
        except Exception as e:  # non-fatal — fall back to no scope enum
            log.warning("programs() query failed (%s)", type(e).__name__)
            self._programs_cache = []
        return self._programs_cache

    # ── connection (per-call; thread-safe for a low-QPS bot) ─────────────────
    # psycopg over host:port — local dev via the cloud-sql-proxy (127.0.0.1:5433),
    # prod via the Cloud SQL private IP (DB_HOST) or a proxy sidecar. A connection
    # pool is the obvious later optimisation.
    def _connect(self):
        import psycopg
        s = self._s
        if not (s.db_host and s.db_user and s.db_pass and s.db_name):
            raise RuntimeError(
                "RAG DB not configured — set DB_HOST/DB_USER/DB_PASS/DB_NAME. Local dev uses the "
                "cloud-sql-proxy (127.0.0.1:5433); prod sets DB_HOST to the Cloud SQL private IP."
            )
        return psycopg.connect(host=s.db_host, port=s.db_port, dbname=s.db_name,
                               user=s.db_user, password=s.db_pass, connect_timeout=10)

    # ── query embedding (RETRIEVAL_QUERY, matches ingest) ────────────────────
    def _embed(self, query: str) -> str:
        from google.genai import types
        from ..utils.timeouts import call_with_timeout
        client = _genai_client(self._s.gcp_project_id, self._s.vertex_location)
        cfg = types.EmbedContentConfig(task_type=_QUERY_TASK, output_dimensionality=self._s.embedding_dimensions)
        resp = call_with_timeout(
            lambda: client.models.embed_content(model=self._s.embedding_model_id, contents=[query], config=cfg),
            timeout=self._s.vertex_timeout_seconds, label="vertex.embed",
        )
        v = list(resp.embeddings[0].values)
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return _vec_literal([x / n for x in v])

    @staticmethod
    def _corpus_values(corpus: CorpusScope) -> list[str]:
        items = [corpus] if isinstance(corpus, Corpus) else list(corpus)
        return [c.value if isinstance(c, Corpus) else str(c) for c in items]

    def _filters(self, channel: str, program_code: str | None) -> tuple[str, dict]:
        clauses = ["corpus = ANY(%(corpora)s)",
                   "(expiry_date IS NULL OR expiry_date >= CURRENT_DATE)"]
        params: dict = {}
        if channel == "customer":                       # §11: SQL access-tier boundary
            clauses.append("access_tier = 'customer'")
            # A page that automated verification flagged is quarantined from the
            # customer channel in SQL, exactly like access_tier (rag-ingestion §4).
            clauses.append("needs_review = false")
        if program_code:                                # §6a branch A/B
            clauses.append("program_code = %(pc)s")
            params["pc"] = program_code
        return " AND ".join(clauses), params

    def retrieve(self, query: str, corpus: CorpusScope, top_k: int = 5, *,
                 program_code: str | None = None, channel: str = "customer") -> list[RetrievalChunk]:
        s = self._s
        corpora = self._corpus_values(corpus)
        if not corpora:
            return []
        try:
            qv = self._embed(query)
        except Exception as e:  # never take down the turn — degrade to no context
            log.warning("query embedding failed (%s); returning no context", type(e).__name__)
            return []

        where, extra = self._filters(channel, program_code)
        params = {"qv": qv, "q": query, "cand": s.rag_hybrid_candidates, "corpora": corpora, **extra}
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(_DENSE_SQL.format(filters=where), params)
                dense = cur.fetchall()
                cols = [d.name for d in cur.description]
                cur.execute(_KEYWORD_SQL.format(filters=where), params)
                keyword = cur.fetchall()
        except Exception as e:
            log.warning("pgvector retrieve failed (%s); returning no context", type(e).__name__)
            return []

        rows = {r[0]: dict(zip(cols, r)) for r in list(dense) + list(keyword)}
        if not rows:
            return []
        rrf: dict[str, float] = {}
        for leg in (dense, keyword):
            for rank, r in enumerate(leg):
                rrf[r[0]] = rrf.get(r[0], 0.0) + 1.0 / (s.rag_rrf_k + rank)

        best_cosine = max(float(r["cosine"]) for r in rows.values())
        if best_cosine < s.rag_relevance_floor:          # §2.5 honest abstention
            log.info("relevance floor: best cosine %.3f < %.2f → abstain", best_cosine, s.rag_relevance_floor)
            return []

        ranked = sorted(rows.values(), key=lambda r: rrf[r["chunk_id"]], reverse=True)[:top_k]
        return [self._to_chunk(r, rrf[r["chunk_id"]]) for r in ranked]

    @staticmethod
    def _to_chunk(r: dict, rrf_score: float) -> RetrievalChunk:
        return RetrievalChunk(
            text=r["content"],
            corpus=r["corpus"],
            ref=r.get("source_uri") or f'{r["doc_id"]}#{r["section"]}',
            score=round(float(r["cosine"]), 4),
            metadata={
                "chunk_id": r["chunk_id"], "program_code": r.get("program_code"),
                "section": r.get("section"), "doc_id": r.get("doc_id"),
                "doc_title": r.get("doc_title"), "version": r.get("version"),
                "content_type": r.get("content_type"), "access_tier": r.get("access_tier"),
                "rrf": round(rrf_score, 5),
            },
        )


class VertexVectorSearchRetriever(Retriever):
    """Not used — pgvector is the locked store (§3). Kept so RAG_BACKEND=vertex
    wiring still constructs; raises rather than silently returning empty."""

    def __init__(self, settings: Settings, namespaces: dict[Corpus, str]):
        self._settings = settings
        self._namespaces = namespaces

    def retrieve(self, query: str, corpus: CorpusScope, top_k: int = 5, *,
                 program_code: str | None = None, channel: str = "customer") -> list[RetrievalChunk]:
        raise NotImplementedError("Vertex Vector Search backend is not used; pgvector is the store (§3).")
