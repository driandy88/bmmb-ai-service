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

import difflib
import re
from typing import Any, Optional

from app.agents.rag.retriever import Corpus, Retriever
from app.agents.rag.synthesize import grounded_answer
from app.config.loader import AppConfig, load_config
from app.config.settings import get_settings
from app.integrations.llm import LLMClient
from app.utils.suggestions import explore_suggestions

_MONEY_RE = re.compile(r"(?:rm\s*)?(\d[\d,]*(?:\.\d+)?)\s*(juta|million|mil|m|k|ribu|thousand)?\b", re.I)
_UNIT_MULT = {"juta": 1e6, "million": 1e6, "mil": 1e6, "m": 1e6, "k": 1e3, "ribu": 1e3, "thousand": 1e3}


def _fmt_amount(amount: float) -> str:
    """Short RM label for prose, e.g. 100000 -> 'RM 100k', 1500000 -> 'RM 1.5m'."""
    if amount >= 1_000_000:
        return f"RM {amount / 1_000_000:.1f}m".replace(".0m", "m")
    if amount >= 1_000:
        return f"RM {amount / 1_000:.0f}k"
    return f"RM {amount:,.0f}"

# Bare replies to the post-answer "apply / talk to our team" offer (stage
# `program_offer`). Decline is checked first so "just looking" doesn't match "ok".
_APPLY_KEYWORDS = ["apply", "sign up", "sign me up", "get started", "go ahead", "proceed",
                   "yes", "yeah", "yep", "sure", "okay", "ok", "sounds good", "sound good",
                   "let's do it", "lets do it", "let's go", "lets go", "interested",
                   "i want", "i'd like", "want to"]
_DECLINE_KEYWORDS = ["no thanks", "no thank", "not now", "maybe later", "not interested",
                     "that's all", "thats all", "nothing else", "no more", "i'm good",
                     "im good", "just looking", "no need"]

# In the post-answer offer, a request to browse OTHER programmes (as opposed to a
# follow-up about the current one). Phrase-based on purpose: a bare "other" inside
# "and other thing" (a follow-up about the SAME programme) must NOT match, only an
# explicit "other/different <programme>" or "what else do you have".
_OTHER_PROGRAMS_RE = re.compile(
    r"\b(?:other|another|different|alternative)\s+"
    r"(?:program|programme|product|financing|facilit\w*|option|scheme|package|offering)s?\b"
    r"|\bwhat(?:'s| is| are)?\s+else\s+(?:do\s+|can\s+)?(?:you|u|we)\s*(?:have|offer|got|provide|do)\b"
    r"|\b(?:show|list|see)\s+(?:me\s+)?(?:all|other|more)\s+(?:program|programme|product|option|financing)s?\b",
    re.I)

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

    def _scoped_program(self, message: str,
                        history: Optional[list] = None) -> tuple[Optional[str], str, bool, list]:
        """-> (program_code, resolved_query, program_dependent, candidates). The history-aware rewrite
        resolves the programme this message names OR inherits from the conversation (§6a), AND
        condenses a follow-up ("what about GGSM?", "and the documents?") into a standalone query used
        for retrieval. Program is None when none applies; resolved_query falls back to the raw message.
        `program_dependent` tells an attribute follow-up apart from a catalog ask (offer stage).
        `candidates` are the listed programmes a mistyped / ambiguous name is close to when no single
        one is confident ("MHIP" → [MIHP-I, MHP-I]) — the caller asks which."""
        programs = getattr(self._retriever, "programs", lambda: [])() or []
        if not programs:
            return None, message, False, []
        try:
            rw = self._llm.rewrite_query(message, programs, history)
        except Exception:  # never break the turn on a rewrite failure
            return None, message, False, []
        valid = {c for c, _ in programs}
        code = rw.get("program_code")
        code = code if code in valid else None
        resolved = (rw.get("rewritten_query") or "").strip() or message
        candidates = [c for c in (rw.get("program_candidates") or []) if c in valid]
        # Deterministic safety net: the rewrite is supposed to flag a mistyped/ambiguous name in
        # `program_candidates`, but the model doesn't do it reliably. When it resolved nothing, fall
        # back to a near-match over the programme list ("MHIP" ≈ MIHP & MHP) so the ambiguity is still
        # caught and clarified — general string similarity, no typo dictionary.
        if not code and not candidates:
            fuzzy = self._fuzzy_candidates(message, programs)
            if len(fuzzy) >= 2:
                candidates = fuzzy
        return code, resolved, bool(rw.get("is_program_dependent")), candidates

    @staticmethod
    def _fuzzy_candidates(message: str, programs: list[tuple[str, str]]) -> list[str]:
        """Which listed programmes a word in the message is a likely TYPO of — general edit-distance
        similarity over the programme codes (normalised to their core, e.g. MIHP-I→MIHP, GGSM3→GGSM),
        not a hardcoded typo list. Exact hits are skipped (the rewrite handles those). Returns the
        near-matched codes, best first; ≥2 means a genuinely ambiguous typo the caller should clarify."""
        cores: dict[str, str] = {}
        for code, _ in programs:
            core = re.sub(r"[-\s]?(?:i|\d+)$", "", code, flags=re.I).upper()
            cores.setdefault(core, code)
        best: dict[str, float] = {}
        for tok in {t.upper() for t in re.findall(r"[A-Za-z]{3,}", message)}:
            for core, code in cores.items():
                if tok == core:  # exact — not a typo
                    continue
                if abs(len(tok) - len(core)) <= 2:
                    r = difflib.SequenceMatcher(None, tok, core).ratio()
                    if r >= 0.7:
                        best[code] = max(best.get(code, 0.0), r)
        return sorted(best, key=lambda c: best[c], reverse=True)

    def _disambiguate(self, candidates: list, slots: dict, message: str,
                      history: Optional[list] = None) -> dict:
        """Ask which programme a mistyped / ambiguous name meant — a tappable chip per candidate, so
        the customer confirms instead of us guessing (or funnelling). The MODEL phrases the question
        (short, natural, straight to the point); `compose` falls back to a concise default offline."""
        titles = dict(getattr(self._retriever, "programs", lambda: [])() or [])

        def _name(code: str) -> str:
            return (titles.get(code) or code).replace(" Sales Kit", "").strip()

        names = [_name(c) for c in candidates]
        joined = f"{names[0]} or {names[1]}" if len(names) == 2 else ", ".join(names[:-1]) + f", or {names[-1]}"
        reply = self._llm.compose(
            "programme_disambiguate", message=message, history=history or [],
            fallback=f"Did you mean {joined}?", candidates="\n".join(f"- {n}" for n in names),
        )
        suggestions = [{"label": _name(c), "value": f"Tell me about {c}"} for c in candidates]
        return _turn(reply, slots, stage="program_done", ui={"type": "none", "payload": {}},
                     suggestions=suggestions)

    @staticmethod
    def _norm_program(code: str) -> str:
        """Strip naming drift so products.yaml 'GGSM' == index 'GGSM3', 'MHP' == 'MHP-I'."""
        norm = re.sub(r"[^A-Z]", "", (code or "").upper())
        return norm[:-1] if norm.endswith("I") else norm

    def _named_unindexed_program(self, message: str) -> Optional[tuple[str, str]]:
        """A programme the bank HAS (products.yaml) but has no Sales Kit indexed for -> (code,
        full_name). None when the message names no known programme, names one we CAN answer (it's
        in the live index), or when there is no index at all (that stays a funnel case)."""
        indexed = {self._norm_program(c) for c, _ in (getattr(self._retriever, "programs", lambda: [])() or [])}
        if not indexed:
            return None
        for p in self._cfg.products.get("quantum", []):
            code = (p.get("program") or "").strip()
            if not code:
                continue
            # match the code with naming-drift suffixes (GGSM3, MHP-I, MIHP-i) as a whole word
            if re.search(rf"\b{re.escape(code)}(?:[-\s]?i|[-\s]?\d+)?\b", message or "", re.IGNORECASE):
                if self._norm_program(code) not in indexed:
                    return code, (p.get("full_name") or code)
        return None

    # -- post-answer offer (apply / talk to our team) ----------------------
    @staticmethod
    def _offer_suggestions(program: str) -> list[dict]:
        """The next-step chips shown under a grounded answer. `value` is what gets
        sent when clicked, so each flows through normal routing (Apply -> INITIATE,
        Talk -> BRANCH/Sales)."""
        return [
            {"label": f"Apply for {program}", "value": f"I'd like to apply for {program}"},
            {"label": "Connect to Sales team", "value": "I'd like to talk to your SME financing team"},
        ]

    @staticmethod
    def _wants_other_programs(message: str) -> bool:
        """True when the customer is asking to see OTHER programmes ('what else do you
        have?', 'other financing options') rather than more about the current one."""
        return bool(_OTHER_PROGRAMS_RE.search(message or ""))

    def _grounded_offer(self, message: str, program: str, slots: dict,
                        history: Optional[list] = None, *,
                        retrieval_query: Optional[str] = None) -> Optional[dict]:
        """A grounded, cited answer about `program` + the apply/talk offer — or None when
        the index has nothing relevant. Shared by a NAMED-programme question and, in the
        offer stage, an anaphoric follow-up that inherits `last_program`. `message` is the
        customer's real question (the synthesiser resolves "what about GGSM?" against `history`);
        `retrieval_query` is the standalone rewrite used only to fetch chunks."""
        ans = grounded_answer(self._llm, self._retriever, message, Corpus.PROGRAM,
                              top_k=4, program_code=program, history=history,
                              retrieval_query=retrieval_query)
        if not ans:
            return None
        slots["last_program"] = program
        # No call-to-action sentence: the offer chips (_offer_suggestions) already carry
        # "Apply for {program}" and "Connect to Sales team", so a "would you like to apply…"
        # line just duplicates them and reads robotic. End on the grounded answer.
        return _turn(ans["reply"], slots, stage="program_offer",
                     ui={"type": "none", "payload": {}}, citations=ans["citations"],
                     sentences=ans["sentences"], grounded=True,
                     suggestions=self._offer_suggestions(program))

    def _followup_decision(self, message: str) -> str:
        """Read a bare reply to the offer: 'apply' | 'decline' | 'other'. An
        explicit 'talk to a person' is caught upstream by the classifier (INS-01)
        and routed to Sales, so here we only resolve proceed vs. decline."""
        low = (message or "").lower().strip()
        if low in ("no", "nope", "nah") or any(k in low for k in _DECLINE_KEYWORDS):
            return "decline"
        if any(k in low for k in _APPLY_KEYWORDS):
            return "apply"
        return "other"

    def _apply_turn(self, program: str, slots: dict) -> dict:
        """Proceed to application for the programme just discussed — named, so the
        customer sees it carried through (not a generic 'the application form')."""
        url = get_settings().new_application_url
        return _turn(
            f"Great — let's get your application for {program} started. "
            "I'll take you to the application form.",
            slots, stage="initiate",
            ui={"type": "open_application_link", "payload": {"url": url, "program": program}},
        )

    def handle(self, message: str, history: list[dict], slots: dict, *,
               stage: Optional[str] = None, intent: Optional[dict] = None) -> dict:
        funnel = self._cfg.products["funnel"]
        slots = dict(slots or {})
        intent_primary = (intent or {}).get("primary")

        # Which specific programme (if any) this turn names — computed once and
        # reused below. A GENERAL question names none and keeps the funnel (§6a:
        # never silently pick one). A bare funnel answer (lone purpose/amount)
        # skips the rewrite so the funnel flows without an extra LLM call.
        if self._is_funnel_nav(message):
            program, resolved, program_dependent, candidates = None, message, False, []
        else:
            program, resolved, program_dependent, candidates = self._scoped_program(message, history)

        # Mistyped / ambiguous programme name ("what about MHIP?" ~ MIHP or MHP): don't guess and
        # don't dump into the funnel — ask which one. The rewrite (LLM) reads the near-matches; we
        # only clarify when it couldn't settle on one AND there are ≥2 candidates.
        if not program and len(candidates) >= 2:
            return self._disambiguate(candidates, slots, message, history)

        # Continuation of a grounded answer's "apply / talk to our team" offer:
        # a bare reply that names no new programme is read as proceed/decline
        # rather than re-classified (which reads "sounds good" as a goodbye —
        # the dead-end we're fixing). Reached via STAGE_TO_ROUTE["program_offer"].
        if stage == "program_offer" and slots.get("last_program") and not program:
            prog = slots["last_program"]
            decision = self._followup_decision(message)
            if decision == "apply":
                return self._apply_turn(prog, slots)
            if decision == "decline":
                return _turn(f"No problem — I'm here whenever you'd like to look at {prog} "
                             "or another programme. A few things I can help with —",
                             slots, stage="program_done", ui={"type": "none", "payload": {}},
                             suggestions=explore_suggestions(prog))
            # A request to browse programmes leaves the current one and opens the discovery funnel
            # below — otherwise "what else do you have?" / "what SME financing do you offer?"
            # dead-ended on an offer to apply for the very programme they were moving on from. Two
            # signals, both the model's: the explicit "other programmes" phrasing, OR a fresh
            # programme question (INS-02) that is NOT programme-dependent — i.e. a catalog/listing
            # ask, not an attribute follow-up ("and the tenure?") about the programme in play.
            browse = self._wants_other_programs(message) or (
                intent_primary == "INS-02" and not program_dependent
            )
            if browse:
                slots.pop("last_program", None)
                # fall through to the funnel
            else:
                # Otherwise it's a follow-up QUESTION about the programme just discussed
                # ("tell me more", "the profit rate", "what documents") — answer it, inheriting
                # last_program so the grounded index is scoped to it even though the message
                # names nothing. This is the anaphora the stateless retriever can't do alone;
                # here we HAVE last_program in slots.
                offer = self._grounded_offer(message, prog, slots, history, retrieval_query=resolved)
                if offer:
                    return offer
                # Nothing relevant to this programme and not asking to browse — keep the
                # thread open with a gentle re-prompt (the original stray-reply behaviour).
                return _turn(f"Sure — would you like to apply for {prog}, or ask me anything "
                             "else about our SME financing?", slots, stage="program_offer",
                             ui={"type": "none", "payload": {}},
                             suggestions=self._offer_suggestions(prog))

        # Grounded program Q&A (Phase 1): when the customer NAMES a programme (a
        # direct question, or the "Details" button), answer it with a grounded,
        # cited answer AND offer the next step so the thread doesn't dead-end.
        if program:
            offer = self._grounded_offer(message, program, slots, history, retrieval_query=resolved)
            if offer:
                return offer

        # A programme we KNOW (products.yaml) but have no Sales Kit indexed for — TERAJU, BIZJAMIN,
        # CGC, SRF… Name it and offer the SME team, instead of silently dropping into the funnel or
        # deflecting as off-topic. (Only when there IS an index; an empty index stays a funnel case.)
        if not self._is_funnel_nav(message):
            named = self._named_unindexed_program(message)
            if named:
                code, full_name = named
                reply = (f"{full_name} is one of Bank Muamalat's SME financing programmes, but I don't "
                         f"have its detailed materials on hand here yet. Our SME financing team can walk you "
                         f"through {code} — or I can help you explore the programmes I can detail.")
                return _turn(reply, slots, stage="program_done", ui={"type": "none", "payload": {}},
                             suggestions=[
                                 {"label": "Connect to Sales team", "value": "I'd like to talk to your SME financing team"},
                                 {"label": "Explore programmes", "value": "What SME financing programmes do you offer?"},
                             ])

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
          sentences: Optional[list] = None, grounded: bool = False,
          suggestions: Optional[list] = None) -> dict:
    return {
        "reply": reply,
        "slots": slots,
        "stage": stage,
        "ui_action": ui,
        "citations": citations or [],
        "sentences": sentences,
        "grounded": grounded,
        "suggestions": suggestions or [],
        "handoff": False,
        "handoff_reason": None,
        "decision_inputs": {"funnel_purpose": slots.get("funnel_purpose"),
                            "funnel_amount": slots.get("funnel_amount"), "stage": stage,
                            "grounded": grounded},
    }
