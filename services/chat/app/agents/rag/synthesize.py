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


def _plain_reply(sentences: list) -> str:
    """Flatten the structured sentences into a plain-text reply for history / logging / channels
    without the structured UI. Bullet items render on their own "- " line so a list still reads as
    a list; labelled facts read "Label: value"; everything else joins as prose."""
    parts = []
    for s in sentences:
        if s.get("bullet"):
            parts.append(f"\n- {s['text']}")
        elif s.get("label"):
            parts.append(f" {s['label']}: {s['text']}")
        else:
            parts.append(f" {s['text']}")
    return "".join(parts).strip()


def grounded_answer(llm, retriever: Retriever, message: str, corpus: CorpusScope, *,
                    top_k: int = 4, channel: str = "customer",
                    program_code: Optional[str] = None,
                    history: Optional[list] = None,
                    retrieval_query: Optional[str] = None) -> Optional[dict]:
    """-> {reply, sentences:[{text,cites}], citations:[…], grounded:True} or None.

    `message` is the customer's ACTUAL question — it goes to the synthesiser, which resolves a
    follow-up ("what about GGSM?", "and the profit rate?") against `history` and answers only that.
    `retrieval_query` (optional) is a standalone rewrite used ONLY to FETCH chunks — better recall on
    a terse follow-up — and is never shown to the synthesiser, so a broad rewrite can't wash out the
    anaphoric intent and turn a focused question into a whole-programme dump."""
    chunks = retriever.retrieve(retrieval_query or message, corpus, top_k=top_k,
                                program_code=program_code, channel=channel)
    if not chunks:
        return None
    out = llm.synthesize_answer(message, chunks, history or []) or {}
    if not out.get("grounded"):
        return None
    sentences = []
    for s in (out.get("sentences") or []):
        text = (s.get("text") or "").strip()
        if not text:
            continue
        entry = {"text": text, "cites": list(s.get("cites") or [])}
        # `label` (optional) marks a key fact for the UI; the lead sentence has none.
        label = (s.get("label") or "").strip()
        if label:
            entry["label"] = label
        # `bullet` (optional) marks a discrete list item — the UI groups a run of these into a <ul>.
        if s.get("bullet"):
            entry["bullet"] = True
        sentences.append(entry)
    if not sentences:
        return None
    citations = [_citation(i, c) for i, c in enumerate(chunks, start=1)]
    reply = _plain_reply(sentences)
    return {"reply": reply, "sentences": sentences, "citations": citations, "grounded": True}
