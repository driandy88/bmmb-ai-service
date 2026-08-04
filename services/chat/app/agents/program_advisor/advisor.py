"""
Program advisor (brief §7) — the Sheet 3 three-question funnel + program RAG.

Deterministic core: purpose + amount are collected across turns (stored in
slots); the requested amount is matched against each program's [min, max]
quantum range (products.yaml, from the Master tab). Purpose only re-orders the
amount-eligible candidates (affinity map). Program RAG enriches via the
Retriever interface only — the agent never constructs a backend.

The LLM (compose) phrases the reply; the deterministic fallback IS the reply
offline. Product SELECTION is never done by the LLM.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.agents.rag.retriever import Corpus, Retriever
from app.agents.rag.synthesize import grounded_answer
from app.config.loader import AppConfig, load_config
from app.integrations.llm import LLMClient

_MONEY_RE = re.compile(r"(?:rm\s*)?(\d[\d,]*(?:\.\d+)?)\s*(juta|million|mil|m|k|ribu|thousand)?\b", re.I)
_UNIT_MULT = {"juta": 1e6, "million": 1e6, "mil": 1e6, "m": 1e6, "k": 1e3, "ribu": 1e3, "thousand": 1e3}


def _fmt_amount(amount: float) -> str:
    """Short RM label for prose, e.g. 100000 -> 'RM 100k', 1500000 -> 'RM 1.5m'."""
    if amount >= 1_000_000:
        return f"RM {amount / 1_000_000:.1f}m".replace(".0m", "m")
    if amount >= 1_000:
        return f"RM {amount / 1_000:.0f}k"
    return f"RM {amount:,.0f}"

# Keyword -> purpose id (Sheet 3, column 1).
_PURPOSE_KEYWORDS = {
    1: ["expansion", "expand", "capital expenditure", "capex", "grow", "growth"],
    2: ["working capital", "cash flow", "cash-flow", "cashflow", "cash gap"],
    3: ["supplier", "suppliers", "trade", "import", "paying suppliers"],
    4: ["machinery", "machine", "vehicle", "equipment", "lorry", "truck"],
    5: ["project", "contract"],
}


class ProgramAdvisor:
    def __init__(self, llm: LLMClient, retriever: Retriever, config: Optional[AppConfig] = None):
        self._llm = llm
        self._retriever = retriever
        self._cfg = config or load_config()

    # -- extraction --------------------------------------------------------
    def _match_purpose(self, message: str) -> Optional[int]:
        low = message.lower()
        for pid, kws in _PURPOSE_KEYWORDS.items():
            if any(kw in low for kw in kws):
                return pid
        m = re.search(r"\b([1-5])\b", low)   # e.g. picked an option number
        return int(m.group(1)) if m else None

    def _parse_amount(self, message: str) -> Optional[float]:
        low = message.lower()
        for m in _MONEY_RE.finditer(low):
            num, unit = m.group(1), m.group(2)
            looks_money = bool(unit) or "rm" in low[max(0, m.start() - 3):m.start() + 3] \
                or "," in num or len(num.replace(",", "").split(".")[0]) >= 4
            if looks_money:
                return float(num.replace(",", "")) * _UNIT_MULT.get((unit or "").lower(), 1.0)
        return None

    # -- candidate resolution (deterministic) ------------------------------
    def _candidates(self, amount: float, purpose: Optional[int]) -> list[dict]:
        eligible = []
        for p in self._cfg.products["quantum"]:
            lo, hi = p.get("min"), p.get("max")
            if (lo is None or amount >= lo) and (hi is None or amount <= hi):
                eligible.append(p)
        if purpose is not None:
            pref = self._cfg.products["funnel"].get("purpose_affinity", {}).get(purpose, [])
            rank = {code: i for i, code in enumerate(pref)}
            eligible.sort(key=lambda p: rank.get(p["program"], 999))
        return eligible

    def _purpose_label(self, pid: int) -> str:
        for o in self._cfg.products["funnel"]["purpose_options"]:
            if o["id"] == pid:
                return o["label"]
        return ""

    # -- grounded-answer gating --------------------------------------------
    def _is_funnel_nav(self, message: str) -> bool:
        """A bare funnel answer — a lone purpose keyword or an amount — that should
        continue the funnel rather than trigger a program lookup."""
        short = len((message or "").split()) <= 6
        return short and (self._match_purpose(message) is not None or self._parse_amount(message) is not None)

    def _scoped_program(self, message: str) -> Optional[str]:
        """The specific programme this message names, resolved against the LIVE index
        via the query-rewrite program extraction (so it survives the products.yaml ↔
        index naming drift, §6a branch A). None if no programme is named."""
        programs = getattr(self._retriever, "programs", lambda: [])() or []
        if not programs:
            return None
        try:
            rw = self._llm.rewrite_query(message, programs)
        except Exception:  # never break the turn on a rewrite failure
            return None
        code = rw.get("program_code")
        return code if code in {c for c, _ in programs} else None

    def handle(self, message: str, history: list[dict], slots: dict) -> dict:
        funnel = self._cfg.products["funnel"]
        slots = dict(slots or {})

        # Grounded program Q&A (Phase 1): when the customer NAMES a specific programme
        # (a direct question, or the "Details" button), answer it with a grounded, cited,
        # program-scoped answer — regardless of any funnel state, so stuck slots can't
        # block it. A GENERAL question naming no programme keeps running the funnel
        # (§6a: never silently pick one programme). Bare funnel answers (a lone purpose
        # or amount) skip this so the funnel flows without an extra rewrite call.
        if not self._is_funnel_nav(message):
            program = self._scoped_program(message)
            if program:
                ans = grounded_answer(self._llm, self._retriever, message, Corpus.PROGRAM,
                                      top_k=4, program_code=program)
                if ans:
                    return _turn(ans["reply"], slots, stage="program_answer",
                                 ui={"type": "none", "payload": {}}, citations=ans["citations"],
                                 sentences=ans["sentences"], grounded=True)

        # Merge any purpose/amount found this turn.
        if slots.get("funnel_purpose") is None:
            pid = self._match_purpose(message)
            if pid is not None:
                slots["funnel_purpose"] = pid
        if slots.get("funnel_amount") is None:
            amt = self._parse_amount(message)
            if amt is not None:
                slots["funnel_amount"] = amt

        purpose = slots.get("funnel_purpose")
        amount = slots.get("funnel_amount")

        # Step 1: purpose.
        if purpose is None:
            options = [o["label"] for o in funnel["purpose_options"]]
            reply = funnel["purpose_prompt"]
            return _turn(reply, slots, stage="funnel_purpose",
                         ui={"type": "show_program_options", "payload": {"step": "purpose", "options": options}})

        # Step 2: amount.
        if amount is None:
            bands = [b["label"] for b in funnel["amount_bands"]]
            reply = funnel["amount_prompt"]
            return _turn(reply, slots, stage="funnel_amount",
                         ui={"type": "show_program_options", "payload": {"step": "amount", "bands": bands}})

        # Step 3: recommend.
        candidates = self._candidates(float(amount), purpose)
        chunks = self._retriever.retrieve(message, Corpus.PROGRAM, top_k=3)
        citations = [{"corpus": c.corpus, "ref": c.ref, "snippet": c.text} for c in chunks]

        purpose_label = self._purpose_label(purpose)
        amount_str = _fmt_amount(float(amount))
        n = len(candidates)

        if not candidates:
            fallback = ("I couldn't match that amount to one of our standard SME financing programs — "
                        "our SME financing team can help find the right fit for you.")
            summary = "(none)"
        else:
            # Prose intro only — the individual programmes are rendered as CARDS by
            # the client (ui_action below), so the reply must NOT list them.
            fallback = (
                f"Based on {purpose_label.lower()} at around {amount_str}, {n} Shariah-compliant "
                f"programme{'s' if n != 1 else ''} fit your profile — they're shown below. "
                "These are indicative; a Bank Muamalat officer confirms your eligibility on a full "
                "application. Would you like to start an application or check your eligibility?"
            )
            summary = f"{n} programme(s) matched · purpose: {purpose_label} · amount: ~{amount_str}"

        reply = self._llm.compose(
            "program_advisor", message=message, history=history, fallback=fallback,
            next_question="", candidates=summary,
            citations="\n".join(c["snippet"] for c in chunks) or "(no snippets)",
        )
        return _turn(
            reply, slots, stage="program_done", citations=citations,
            ui={"type": "show_program_options",
                "payload": {"step": "result", "purpose": purpose_label,
                            "amount": amount, "products": [c["program"] for c in candidates]}},
        )


def _turn(reply: str, slots: dict, *, stage: str, ui: dict, citations: Optional[list] = None,
          sentences: Optional[list] = None, grounded: bool = False) -> dict:
    return {
        "reply": reply,
        "slots": slots,
        "stage": stage,
        "ui_action": ui,
        "citations": citations or [],
        "sentences": sentences,
        "grounded": grounded,
        "handoff": False,
        "handoff_reason": None,
        "decision_inputs": {"funnel_purpose": slots.get("funnel_purpose"),
                            "funnel_amount": slots.get("funnel_amount"), "stage": stage,
                            "grounded": grounded},
    }
