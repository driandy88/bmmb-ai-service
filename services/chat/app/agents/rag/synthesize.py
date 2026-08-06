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


def grounded_answer(llm, retriever: Retriever, message: str, corpus: CorpusScope, *,
                    top_k: int = 4, channel: str = "customer",
                    program_code: Optional[str] = None) -> Optional[dict]:
    """-> {reply, sentences:[{text,cites}], citations:[…], grounded:True} or None."""
    chunks = retriever.retrieve(message, corpus, top_k=top_k,
                                program_code=program_code, channel=channel)
    if not chunks:
        return None
    out = llm.synthesize_answer(message, chunks) or {}
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
        sentences.append(entry)
    if not sentences:
        return None
    citations = [_citation(i, c) for i, c in enumerate(chunks, start=1)]
    # Plain-text reply (history / logging / non-structured channels): "Label: value" reads naturally.
    reply = " ".join(f"{s['label']}: {s['text']}" if s.get("label") else s["text"] for s in sentences)
    return {"reply": reply, "sentences": sentences, "citations": citations, "grounded": True}
