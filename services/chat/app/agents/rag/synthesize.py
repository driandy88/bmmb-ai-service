"""
Grounded answer helper (Phase 1) — retrieve → synthesise → cite.

Shared by the program-advisor and guidelines agents so a factual question becomes
a grounded, cited answer with ZERO duplicated logic. Returns None when the corpus
is silent (no chunks) or the synthesiser can't ground the answer — the caller then
runs its own fallback (funnel / Sales handoff).

The retriever has ALREADY filtered corpus / access_tier / freshness / needs_review /
program (§6a, §11), so every chunk here is safe to quote. The citation numbers are
1-based and stable within the turn; the UI renders one chip per number.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from app.agents.rag.retriever import CorpusScope, Retriever

_PAGE_RE = re.compile(r"#page=(\d+)")


def _citation(n: int, c: Any) -> dict:
    md = getattr(c, "metadata", None) or {}
    ref = getattr(c, "ref", "") or ""
    page_match = _PAGE_RE.search(ref)
    score = getattr(c, "score", None)
    return {
        "n": n,
        "corpus": getattr(c, "corpus", None),
        "ref": ref,
        "snippet": getattr(c, "text", "") or "",
        "doc_id": md.get("doc_id"),
        "doc_title": md.get("doc_title"),
        "section": md.get("section"),
        "page": int(page_match.group(1)) if page_match else None,
        "score": round(float(score), 4) if score is not None else None,
        "access_tier": md.get("access_tier"),
    }


# Deterministic formatting backstop. The synthesis prompt already says "rephrase, don't transcribe —
# no markdown tables/headings/bullet glyphs", but gemini-flash still copies source markup through on
# broad questions (a whole "### SECTION" or a "| a | b |" table lands verbatim in a sentence). Rather
# than tighten a probabilistic instruction yet again, strip the markup here so the UI — which renders
# each sentence as plain text — can NEVER show raw `###`, `**`, `|`, or `---`. Wording is left intact;
# only formatting symbols are removed, so it changes nothing on already-clean sentences.
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_BOLD_ALT = re.compile(r"__([^_]+)__")
_MD_HEADING = re.compile(r"#{1,6}\s*")
_MD_RULE = re.compile(r":?-{3,}:?")
_MD_PIPE = re.compile(r"\s*\|\s*")
_MD_BULLET = re.compile(r"(^|\s)[•·*]\s+")
_MULTI_COMMA = re.compile(r"(?:,\s*){2,}")
_WS = re.compile(r"\s{2,}")


def _clean_prose(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^['‘’]+\s*", "", t)   # stray leading quote the extractor sometimes leaves
    t = _MD_BOLD.sub(r"\1", t)
    t = _MD_BOLD_ALT.sub(r"\1", t)
    t = t.replace("**", "").replace("`", "")     # drop any unclosed bold / code ticks
    t = _MD_HEADING.sub("", t)                   # "### PURPOSE" / "#…" -> drop the hashes
    t = _MD_RULE.sub(" ", t)                     # --- / :--- table rules
    t = _MD_PIPE.sub(", ", t)                    # flatten a copied table's cells to "a, b, c"
    t = _MD_BULLET.sub(r"\1", t)                 # inline bullet glyphs
    t = _MULTI_COMMA.sub(", ", t)                # collapse ", , ," from empty table cells
    t = re.sub(r"^[\s,;]+", "", t)               # leading punctuation left by a stripped marker
    t = _WS.sub(" ", t)
    return t.strip()


def _plain_reply(sentences: list) -> str:
    """Flatten the structured sentences into a plain-text reply for history / logging / channels
    without the structured UI. Bullet items render on their own "- " line so a list still reads as
    a list; everything else joins as prose."""
    parts = []
    for s in sentences:
        if s.get("bullet"):
            parts.append(f"\n- {s['text']}")
        else:
            parts.append(f" {s['text']}")
    return "".join(parts).strip()


def grounded_answer(llm, retriever: Retriever, message: str, corpus: CorpusScope, *,
                    top_k: int = 4, channel: str = "customer",
                    program_code: Optional[str] = None,
                    program_codes: Optional[list] = None,
                    history: Optional[list] = None,
                    retrieval_query: Optional[str] = None) -> Optional[dict]:
    """-> {reply, sentences:[{text,cites}], citations:[…], grounded:True} or None.

    `message` is the customer's ACTUAL question — it goes to the synthesiser, which resolves a
    follow-up ("what about GGSM?", "and the profit rate?") against `history` and answers only that.
    `retrieval_query` (optional) is a standalone rewrite used ONLY to FETCH chunks — better recall on
    a terse follow-up — and is never shown to the synthesiser, so a broad rewrite can't wash out the
    anaphoric intent and turn a focused question into a whole-programme dump.
    `program_codes` (optional) scopes a MULTI-programme answer (a compare) to exactly those programmes
    — we fetch chunks per code and combine, so an unscoped search can't drag in a confusable
    neighbour (MIHP-i leaking into a GGSM-vs-MHP-i compare) and answer about the wrong product."""
    query = retrieval_query or message
    if program_codes:
        # A compare fetches chunks per programme (so a look-alike neighbour can't leak in). Those
        # retrievals are independent I/O (each embeds + queries), so run them CONCURRENTLY — a compare
        # was doing them back-to-back and paying ~2s twice. Order is preserved so citations stay stable.
        per = max(2, top_k // len(program_codes))
        with ThreadPoolExecutor(max_workers=len(program_codes)) as ex:
            per_code = ex.map(
                lambda code: retriever.retrieve(query, corpus, top_k=per, program_code=code, channel=channel),
                program_codes,
            )
        chunks = [c for result in per_code for c in result]
    else:
        chunks = retriever.retrieve(query, corpus, top_k=top_k, program_code=program_code, channel=channel)
    if not chunks:
        return None
    out = llm.synthesize_answer(message, chunks, history or []) or {}
    if not out.get("grounded"):
        return None
    sentences = []
    for s in (out.get("sentences") or []):
        text = _clean_prose(s.get("text") or "")
        if not text:
            continue
        entry = {"text": text, "cites": list(s.get("cites") or [])}
        # `bullet` (optional) marks a discrete list item — the UI groups a run of these into a <ul>.
        if s.get("bullet"):
            entry["bullet"] = True
        sentences.append(entry)
    if not sentences:
        return None
    citations = [_citation(i, c) for i, c in enumerate(chunks, start=1)]
    reply = _plain_reply(sentences)
    return {"reply": reply, "sentences": sentences, "citations": citations, "grounded": True}
