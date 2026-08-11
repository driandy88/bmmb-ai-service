"""
RewriteScopingRetriever — query rewrite + program scoping BEHIND the Retriever
interface (§6 step 1–3, §6a), so RAG_BACKEND=pgvector gains §6a with ZERO agent or
orchestrator edits. `get_retriever()` wraps the real backend with this.

Per call it: rewrites the customer's query (normalise loan→financing,
interest→profit rate — §2.1 at query time) and extracts an explicitly-named
programme, then delegates to the inner backend scoped to that programme.

Honest limit: it sees only the query string — the frozen `retrieve()` carries no
history or session — so it does SINGLE-UTTERANCE scoping only. Branch A (the
customer names a programme) works; branch B (inherited `last_program` / "what about
that one?") needs context the interface does not provide, which would require an
agent/orchestrator edit. See the Phase 6 notes.
"""
from __future__ import annotations

from .retriever import CorpusScope, RetrievalChunk, Retriever
from ...integrations.llm import LLMClient
from ...utils.logging import get_logger

log = get_logger("rag.rewrite")


class RewriteScopingRetriever(Retriever):
    def __init__(self, inner: Retriever, llm: LLMClient):
        self._inner = inner
        self._llm = llm

    def programs(self) -> list[tuple[str, str]]:
        fn = getattr(self._inner, "programs", None)
        return fn() if callable(fn) else []

    def retrieve(self, query: str, corpus: CorpusScope, top_k: int = 5, *,
                 program_code: str | None = None, channel: str = "customer") -> list[RetrievalChunk]:
        programs = self.programs()
        valid = {c for c, _ in programs}
        # The understand path has ALREADY produced a standalone retrieval query and resolved the
        # programme, so it passes `program_code` explicitly. Re-running the rewrite LLM here is pure
        # redundant work — its inferred code is discarded in favour of the caller's, and it adds a whole
        # extra Gemini round-trip (the slowest, most spike-prone step) to every grounded/compare turn.
        # Skip it when the caller scoped the query; keep it only when nothing was passed (the funnel /
        # guidelines paths that still rely on the wrapper for §6 scoping).
        if program_code is not None:
            return self._inner.retrieve(query, corpus, top_k, program_code=program_code, channel=channel)
        try:
            rw = self._llm.rewrite_query(query, programs)
        except Exception as e:  # never break retrieval on a rewrite failure
            log.warning("rewrite failed (%s); passing query through", type(e).__name__)
            rw = {"rewritten_query": query, "program_code": None, "is_program_dependent": None}

        rewritten = rw.get("rewritten_query") or query
        # Caller's explicit program wins; otherwise the inferred one, but only if it
        # is a real indexed code — drop hallucinated / unknown codes to stay unscoped.
        inferred = rw.get("program_code")
        pc = program_code or (inferred if inferred in valid else None)
        log.info("rewrite %r -> %r | program=%s dep=%s", query, rewritten, pc, rw.get("is_program_dependent"))
        return self._inner.retrieve(rewritten, corpus, top_k, program_code=pc, channel=channel)
