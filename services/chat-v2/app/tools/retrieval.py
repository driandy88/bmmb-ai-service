"""
`search_knowledge` — grounded retrieval over the BMMB corpora.

Differences from v1's RAG path, both deliberate:

1. **No query-rewrite LLM call.** v1 wraps the retriever in RewriteScopingRetriever,
   which spends a model call working out which programme the message is about.
   The agent already knows — it has the conversation — so it passes `programme`
   as an argument. One round trip saved, and it handles anaphora ("what about its
   profit rate?") better than a rewrite over a single message can.

2. **No separate synthesis call.** v1 retrieves, then makes a second structured
   call that writes cited sentences as JSON. Here the numbered sources go back to
   the agent and it writes the cited answer in the same turn it was already going
   to take.

Retrieval itself is the vendored v1 implementation, unchanged, against the same
pgvector index — so an A/B measures the approach, not the search.
"""
from __future__ import annotations

from functools import lru_cache

from langchain_core.tools import tool

from app.agents.rag.retriever import Corpus, Retriever, StubRetriever
from app.config.settings import get_settings
from app.config.settings_v2 import get_v2_settings
from app.runtime.context import current
from app.utils.logging import get_logger

log = get_logger("tools.retrieval")

# Same namespaces as v1's corpora.py. Searched together: a Shariah question about
# a specific programme legitimately spans both, and making the agent choose a
# corpus is a knob that adds failure modes without adding capability.
_SCOPE = [Corpus.PROGRAM, Corpus.GUIDELINES_SHARIAH]
_NAMESPACES = {
    Corpus.PROGRAM: "bmmb-sme-program",
    Corpus.GUIDELINES_SHARIAH: "bmmb-sme-guidelines-shariah",
    Corpus.SALES_DIR: "bmmb-sme-sales-dir",
}


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    """Build the retriever once. Unlike v1's factory this does NOT wrap the
    backend in the rewrite/scoping layer — see the module docstring."""
    s = get_settings()
    if s.rag_backend == "pgvector":
        from app.integrations.vector_search import PgVectorRetriever
        return PgVectorRetriever(s, _NAMESPACES)
    if s.rag_backend == "vertex":
        from app.integrations.vector_search import VertexVectorSearchRetriever
        return VertexVectorSearchRetriever(s, _NAMESPACES)
    if s.rag_backend != "stub":
        log.warning("Unknown RAG_BACKEND=%r; using StubRetriever.", s.rag_backend)
    return StubRetriever()


def known_programmes() -> list[tuple[str, str]]:
    """(code, title) pairs actually present in the index. Learned from the index
    rather than products.yaml, because the two drift and the index is what
    retrieval can actually be scoped to."""
    r = get_retriever()
    getter = getattr(r, "programs", None)
    return list(getter()) if callable(getter) else []


@tool
def search_knowledge(question: str, programme: str = "") -> str:
    """Search Bank Muamalat's SME financing knowledge base and return numbered sources.

    Use this for ANY factual question — profit rates, financing amounts, tenure,
    eligibility criteria, required documents, fees, or Shariah policy. Never answer
    such a question without calling this first.

    Cite the numbers it returns inline in your reply, like [1] and [2].

    Args:
        question: what to look up, as a complete question. If the customer's
            wording is vague or refers back to something earlier ("what about the
            rate?"), rewrite it into a standalone question first.
        programme: optional programme code to scope the search to, e.g. "GGSM".
            Pass it when the customer named a programme or you are following up on
            one already discussed. Leave empty to search everything.
    """
    ctx = current()
    v2 = get_v2_settings()
    code = (programme or "").strip() or None

    try:
        chunks = get_retriever().retrieve(
            question, _SCOPE, top_k=v2.search_top_k, program_code=code, channel="customer",
        )
    except Exception as exc:  # never surface a stack trace into the conversation
        log.warning("retrieval failed (%s)", type(exc).__name__)
        ctx.record("search_knowledge", programme=code, result="error")
        return "SEARCH_FAILED: the knowledge base could not be reached. Tell the customer you cannot look that up right now and offer the SME financing team."

    if not chunks:
        ctx.record("search_knowledge", programme=code, result="empty")
        return "NO_RESULTS: nothing in the knowledge base answers this. Do not guess — say you don't have that detail and offer the SME financing team."

    numbers = ctx.add_citations(chunks)
    ctx.record("search_knowledge", programme=code, result="ok", chunks=len(chunks))

    lines = []
    for n, c in zip(numbers, chunks):
        title = (c.metadata or {}).get("doc_title") or c.corpus
        section = (c.metadata or {}).get("section")
        header = f"[{n}] {title}" + (f" — {section}" if section else "")
        lines.append(f"{header}\n{c.text}")
    return (
        "SOURCES (cite these numbers inline, e.g. [1]). Use ONLY what is written here; "
        "preserve figures exactly:\n\n" + "\n\n".join(lines)
    )