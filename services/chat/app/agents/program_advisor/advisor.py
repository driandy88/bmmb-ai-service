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

from ...agents.rag.retriever import Corpus, Retriever
from ...agents.rag.synthesize import grounded_answer
from ...config.loader import AppConfig, load_config
from ...config.settings import get_settings
from ...integrations.llm import LLMClient, normalize_understanding
from ...utils.suggestions import explore_suggestions

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
                      history: Optional[list] = None, resolved: str = "") -> dict:
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
        # Each chip re-sends the turn with only the mistyped name corrected, so the pick keeps the
        # topic. Prefer `resolved` (the rewrite's history-aware query, e.g. "documents for MHIP") over
        # the raw message: it carries the attribute, so the pipeline needn't re-derive it across the
        # clarify turn — which is where it otherwise drops to a bare overview.
        base = resolved if self._ambiguous_token(resolved, candidates) else message
        typo = self._ambiguous_token(base, candidates)

        def _pick(code: str) -> str:
            if typo:
                return re.sub(re.escape(typo), code, base, count=1, flags=re.I)
            return f"Tell me about {code}"

        suggestions = [{"label": _name(c), "value": _pick(c)} for c in candidates]
        return _turn(reply, slots, stage="program_done", ui={"type": "none", "payload": {}},
                     suggestions=suggestions)

    @staticmethod
    def _ambiguous_token(message: str, candidates: list) -> Optional[str]:
        """The mistyped programme word in the message — the token most similar to the candidates'
        cores — so a chip can swap just that word for the chosen code and leave the rest of the
        customer's phrasing (and thus the topic) intact. None if nothing in the message is close."""
        cores = [re.sub(r"[-\s]?(?:i|\d+)$", "", c, flags=re.I).upper() for c in candidates]
        best, token = 0.0, None
        for tok in re.findall(r"[A-Za-z]{3,}", message):
            score = max(difflib.SequenceMatcher(None, tok.upper(), core).ratio() for core in cores)
            if score > best:
                best, token = score, tok
        return token if best >= 0.6 else None

    @staticmethod
    def _norm_program(code: str) -> str:
        """Strip naming drift so products.yaml 'GGSM' == index 'GGSM3', 'MHP' == 'MHP-I'."""
        norm = re.sub(r"[^A-Z]", "", (code or "").upper())
        return norm[:-1] if norm.endswith("I") else norm

    @staticmethod
    def _program_aliases(code: str, full_name: str) -> list[str]:
        """The names a customer might type for a programme: its code PLUS any abbreviation the bank
        shows in parentheses in the full name (e.g. 'TERAJU … (BECF)' → also 'BECF'). Config-derived,
        so we recognise exactly the names we display — a name straight off our own catalog is never a
        stranger. (Without this, 'BECF tenure?' fell through to a generic help reply.)"""
        aliases = [code] + re.findall(r"\(([^)]+)\)", full_name or "")
        return [a.strip() for a in aliases if a and a.strip()]

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
            full_name = p.get("full_name") or code
            # match the code OR any displayed abbreviation (BECF for TERAJU), tolerating the
            # naming-drift suffix (GGSM3, MHP-I, MIHP-i), as a whole word.
            for name in self._program_aliases(code, full_name):
                if re.search(rf"\b{re.escape(name)}(?:[-\s]?i|[-\s]?\d+)?\b", message or "", re.IGNORECASE):
                    if self._norm_program(code) not in indexed:
                        return code, full_name
                    break   # named programme IS indexed → answerable; stop checking its aliases
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
        # No call-to-action sentence: the offer chips already carry Apply / Connect-to-Sales, so a
        # "would you like to apply…" line just duplicates them and reads robotic.
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

    def _capability_turn(self, slots: dict) -> dict:
        """Answer "what can you do?" / "can you compare?" from our own capabilities — a truthful
        overview + tappable next steps, instead of dropping into the funnel. The list is factual
        (what the assistant actually supports), so it's deterministic; the LLM chose to ask, we answer."""
        reply = (
            "Happy to help — here's what I can do:\n"
            "• Answer questions about our SME financing programmes — profit rate, tenure, financing "
            "size, eligibility, documents — with sources\n"
            "• Compare two programmes\n"
            "• Recommend one based on what you need the financing for and how much\n"
            "• Give an in-principle eligibility indication\n"
            "• Start, continue, or track an application\n"
            "• Connect you to our SME financing team\n\n"
            "What would you like to do?"
        )
        return _turn(reply, slots, stage="program_done", ui={"type": "none", "payload": {}},
                     suggestions=[
                         {"label": "Discover programmes", "value": "What SME financing programmes do you offer?"},
                         {"label": "Check eligibility", "value": "Am I eligible for SME financing?"},
                         {"label": "Apply", "value": "I'd like to apply for SME financing"},
                     ])

    def _catalog_turn(self, message: str, history: Optional[list], slots: dict) -> dict:
        """List the programmes we offer — a direct catalog answer for "what else / what do you have /
        do you have a loan product?", instead of the funnel. The LIST is our real catalog (products.yaml);
        the LLM decided it's a listing question and phrases the reply (incl. our Islamic 'financing not
        loan' framing). Config-grounded, LLM-phrased — not a hardcoded canned reply.

        Honest about depth: the list is SPLIT into the programmes we can detail right now (in the live
        index) and the ones the SME team covers — so the catalog never invites a question we then have
        to deflect (the "you listed BECF, but can't tell me about it" problem)."""
        quantum = self._cfg.products.get("quantum", [])
        indexed = {self._norm_program(c) for c, _ in (getattr(self._retriever, "programs", lambda: [])() or [])}

        def _name(p: dict) -> str:
            return (p.get("full_name") or p.get("program") or "").strip()

        detailed = [_name(p) for p in quantum if _name(p) and self._norm_program(p.get("program") or "") in indexed]
        others = [_name(p) for p in quantum if _name(p) and self._norm_program(p.get("program") or "") not in indexed]

        if indexed and detailed and others:
            programmes = (
                "Programmes I can give full details on now:\n"
                + "\n".join(f"- {n}" for n in detailed)
                + "\n\nAlso available — our SME financing team can walk you through these:\n"
                + "\n".join(f"- {n}" for n in others)
            )
            fallback = (
                'As an Islamic bank, we offer Shariah-compliant SME financing (we say "financing" '
                'rather than a conventional "loan").\n\n' + programmes +
                "\n\nWould you like details on any of the first group, or shall I help you find the best fit?"
            )
        else:
            # No live index (offline/stub) or everything is on one side → a single flat list.
            programmes = "\n".join(f"- {n}" for n in (detailed + others))
            fallback = (
                'As an Islamic bank, we offer Shariah-compliant SME financing (we say "financing" '
                'rather than a conventional "loan"). Our programmes include:\n' + programmes +
                "\n\nWould you like details on any of these, or shall I help you find the best fit?"
            )
        reply = self._llm.compose("catalog", message=message, history=history or [], fallback=fallback,
                                  programmes=programmes)
        return _turn(reply, slots, stage="program_done", ui={"type": "none", "payload": {}},
                     suggestions=[
                         {"label": "Help me choose", "value": "Help me find the right SME financing"},
                         {"label": "Check eligibility", "value": "Am I eligible for SME financing?"},
                         {"label": "Talk to our team", "value": "I'd like to talk to your SME financing team"},
                     ])

    def _unindexed_redirect(self, code: str, full_name: str, slots: dict) -> dict:
        """Name a programme the bank HAS but we can't detail here (no Sales Kit indexed), and hand to
        Sales — never fabricate its facts, and never dump the customer into the funnel for asking."""
        reply = (f"{full_name} is one of Bank Muamalat's SME financing programmes, but I don't "
                 f"have its detailed materials on hand here yet. Our SME financing team can walk you "
                 f"through {code} — or I can help you explore the programmes I can detail.")
        return _turn(reply, slots, stage="program_done", ui={"type": "none", "payload": {}},
                     suggestions=[
                         {"label": "Connect to Sales team", "value": "I'd like to talk to your SME financing team"},
                         {"label": "Explore programmes", "value": "What SME financing programmes do you offer?"},
                     ])

    def _soft_help(self, slots: dict) -> dict:
        """The turn didn't map to a programme, an action, or a request for guidance. Rather than
        ambush the customer with the Program Finder wizard — which is what made it a non-sequitur —
        offer a warm, concrete hand and let THEM choose the direction. Deterministic + honest; the
        guided funnel is now behind an explicit 'Help me choose', not the fallback for everything."""
        reply = (
            "Happy to help with Bank Muamalat SME financing. I can tell you about a specific "
            "programme — profit rate, tenure, financing size, eligibility, documents — help you find "
            "the right one for what you need, or connect you with our SME financing team. "
            "What would be most useful?"
        )
        return _turn(reply, slots, stage="program_done", ui={"type": "none", "payload": {}},
                     suggestions=[
                         {"label": "Discover programmes", "value": "What SME financing programmes do you offer?"},
                         {"label": "Help me choose", "value": "Help me find the right SME financing"},
                         {"label": "Talk to our team", "value": "I'd like to talk to your SME financing team"},
                     ])

    # -- Phase 1: one-understanding path (settings.use_understand) --------------
    def _grounded_compare(self, message: str, slots: dict, history: Optional[list],
                          retrieval_query: str, codes: Optional[list] = None) -> Optional[dict]:
        """A grounded comparison across the named programmes. Retrieval is scoped to EACH of `codes`
        (fetched per kit and combined) so a confusable neighbour — MIHP-i vs MHP-i — can't leak in
        from an unscoped search and get compared by mistake. Offers Sales (no single 'Apply for X')."""
        ans = grounded_answer(self._llm, self._retriever, message, Corpus.PROGRAM,
                              top_k=6, program_codes=codes, history=history, retrieval_query=retrieval_query)
        if not ans:
            return None
        return _turn(ans["reply"], slots, stage="program_offer", ui={"type": "none", "payload": {}},
                     citations=ans["citations"], sentences=ans["sentences"], grounded=True,
                     suggestions=[{"label": "Connect to Sales team",
                                   "value": "I'd like to talk to your SME financing team"}])

    def _handle_understand(self, message: str, history: list[dict], slots: dict, *,
                           stage: Optional[str] = None, sig: Optional[dict] = None) -> dict:
        """Act on the turn's understanding, reusing the SAME terminal handlers as the current path
        (`_disambiguate` / `_grounded_offer` / `_apply_turn` / `_funnel_reply`). The keyword
        interpreters and the double rewrite are gone; the fuzzy net and money regex stay only as
        deterministic backstops. In Phase 2 the signal is computed ONCE upstream (classify_node) and
        passed in via `sig`; if absent (Phase-1 style call), we compute it here. Everything upstream
        (guardrail/classify/decide) and downstream (grounding, eligibility, tiers) is untouched."""
        programs = getattr(self._retriever, "programs", lambda: [])() or []
        valid = {c for c, _ in programs}
        if sig is None:
            try:
                sig = self._llm.understand(message, history, programs=programs, stage=stage or "")
            except Exception:  # never break the turn on an understand failure
                sig = {}
        sig = normalize_understanding(sig)

        program = sig["program_code"] if sig["program_code"] in valid else None
        retrieval_query = (sig.get("retrieval_query") or "").strip() or message
        # A confident compare of two known programmes — "difference between MHP-i and MIHP-i". Computed
        # up front because it must win over the fuzzy disambiguation backstop below: MHP-i and MIHP-i
        # are near look-alikes, so that backstop would otherwise flag them and ask "which one?" on a
        # turn where the customer clearly named BOTH and wants them compared.
        cmp_codes = [c for c in sig["compare_programs"] if c in valid]
        is_compare = sig["turn_type"] == "compare" and len(cmp_codes) >= 2

        # 1. Ambiguous / mistyped name → clarify with topic-carrying chips. Signal first; the
        #    deterministic near-match is a backstop for when the model doesn't flag it. Skipped for a
        #    confident compare (see above) — that names both, so compare instead of asking which.
        cands = [c for c in sig["disambiguation"]["candidates"] if c in valid]
        if not program and not cands:
            fuzzy = self._fuzzy_candidates(message, programs)
            if len(fuzzy) >= 2:
                cands = fuzzy
        if not program and not is_compare and len(cands) >= 2:
            return self._disambiguate(cands, slots, message, history, retrieval_query)

        # 2. Genuinely unsure → clarify with the model's own question (never guess).
        if sig["clarify"]["needed"] and (sig["clarify"]["question"] or "").strip():
            return _turn(sig["clarify"]["question"].strip(), slots, stage="program_done",
                         ui={"type": "none", "payload": {}})

        # 2b. "What can you do?" / "can you compare?" — say what we can help with, don't funnel.
        if sig["turn_type"] == "capability":
            return self._capability_turn(slots)

        # 2c. "What else do you have / do you have a loan product?" — list the catalog, don't funnel.
        if sig["turn_type"] == "catalog":
            return self._catalog_turn(message, history, slots)

        # 2d. Compare two programmes we can detail — a clear, typed intent that must win even in the
        #     offer stage. (Otherwise the offer-followup heuristic below reads "compare GGSM and MHP-i"
        #     as a question about the last programme and reprompts instead of comparing.) Retrieval is
        #     scoped to exactly these codes so a confusable neighbour can't be compared by mistake.
        if is_compare:
            cmp = self._grounded_compare(message, slots, history, retrieval_query, cmp_codes)
            if cmp:
                return cmp

        # 3. Post-answer offer: read by MEANING (apply/decline/other), not a keyword list.
        browse_other = False
        if stage == "program_offer" and slots.get("last_program"):
            prog = slots["last_program"]
            resp = sig["offer_response"]
            if resp == "apply":
                return self._apply_turn(prog, slots)
            if resp == "decline":
                return _turn(f"No problem — I'm here whenever you'd like to look at {prog} "
                             "or another programme. A few things I can help with —",
                             slots, stage="program_done", ui={"type": "none", "payload": {}},
                             suggestions=explore_suggestions(prog))
            if resp == "other":
                slots.pop("last_program", None)   # they want to browse OTHER programmes → discovery
                browse_other = True
            elif not program:
                # attribute follow-up about the programme in play ("and the tenure?")
                offer = self._grounded_offer(message, prog, slots, history, retrieval_query=retrieval_query)
                if offer:
                    return offer
                return _turn(f"Sure — would you like to apply for {prog}, or ask me anything "
                             "else about our SME financing?", slots, stage="program_offer",
                             ui={"type": "none", "payload": {}},
                             suggestions=self._offer_suggestions(prog))

        # 4. Known-but-unindexed programme the customer NAMED → be honest and hand to Sales. Checked on
        #    the raw message (not the model's program_code) so an anaphora / mis-resolve to an indexed
        #    code can't hide the fact they literally asked about SRF / TERAJU / CGC.
        named = self._named_unindexed_program(message)
        if named and not program:
            return self._unindexed_redirect(named[0], named[1], slots)

        # 6. Grounded programme answer (the common case).
        if program:
            offer = self._grounded_offer(message, program, slots, history, retrieval_query=retrieval_query)
            if offer:
                return offer
            # Resolved a programme but the index had nothing. If the customer actually named a
            # programme we DON'T detail (the model mis-resolved via anaphora), say so honestly rather
            # than dropping them into the funnel — the exact ambush behind "what about SRF".
            if named:
                return self._unindexed_redirect(named[0], named[1], slots)

        # 7. No programme in play. What the customer WANTS decides the reply — the guided Program
        #    Finder is a tool for "help me choose", NOT a catch-all for everything else, which is what
        #    made it ambush people mid-conversation. Merge any purpose/amount the model read (exact
        #    figure regex as a backstop) so a guidance turn flows straight into the recommender.
        if slots.get("funnel_purpose") is None and sig["funnel"]["purpose_id"]:
            slots["funnel_purpose"] = sig["funnel"]["purpose_id"]
        if slots.get("funnel_amount") is None:
            amt = sig["funnel"]["amount_rm"]
            if amt is None:
                amt = self._parse_amount(message)
            if amt is not None:
                slots["funnel_amount"] = amt

        wants_guidance = (
            sig["turn_type"] in ("recommend", "eligibility")   # asked to be pointed to one
            or browse_other                                    # left a programme to see the others
            or stage in ("funnel_purpose", "funnel_amount")    # already mid-funnel
            or slots.get("funnel_purpose") is not None         # gave a purpose to match on
            or slots.get("funnel_amount") is not None          # gave an amount to match on
            or not programs                                    # no index at all → the config recommender is all we have
        )
        if wants_guidance:
            return self._funnel_reply(message, history, slots)
        return self._soft_help(slots)

    def handle(self, message: str, history: list[dict], slots: dict, *,
               stage: Optional[str] = None, intent: Optional[dict] = None,
               understanding: Optional[dict] = None) -> dict:
        slots = dict(slots or {})
        # Phase 1/2: the one-understanding path (off by default). `understanding` is the signal the
        # classify node already computed this turn (Phase 2); if absent we compute it. Legacy below.
        if get_settings().use_understand:
            return self._handle_understand(message, history, slots, stage=stage, sig=understanding)
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
            return self._disambiguate(candidates, slots, message, history, resolved)

        # Continuation of a grounded answer's "apply / talk to our team" offer:
        # a bare reply that names no new programme is read as proceed/decline
        # rather than re-classified (which reads "sounds good" as a goodbye).
        # Reached via STAGE_TO_ROUTE["program_offer"].
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
            # Browse OTHER programmes → drop the current one and open the funnel; else a
            # dead-end offer to apply for the programme they're moving on from. Two signals, both the
            # model's: explicit "other programmes" phrasing, OR a fresh INS-02 that is NOT
            # programme-dependent (a catalog ask, not an attribute follow-up about the one in play).
            browse = self._wants_other_programs(message) or (
                intent_primary == "INS-02" and not program_dependent
            )
            if browse:
                slots.pop("last_program", None)
                # fall through to the funnel
            else:
                # A follow-up QUESTION about the programme in play — answer it, inheriting last_program
                # so retrieval stays scoped even though the message names nothing (the anaphora the
                # stateless retriever can't do; here we HAVE last_program).
                offer = self._grounded_offer(message, prog, slots, history, retrieval_query=resolved)
                if offer:
                    return offer
                # Not answerable and not a browse — keep the thread open with a gentle re-prompt.
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

        if slots.get("funnel_purpose") is None:
            pid = self._match_purpose(message)
            if pid is not None:
                slots["funnel_purpose"] = pid
        if slots.get("funnel_amount") is None:
            amt = self._parse_amount(message)
            if amt is not None:
                slots["funnel_amount"] = amt

        return self._funnel_reply(message, history, slots)

    def _funnel_reply(self, message: str, history: list[dict], slots: dict) -> dict:
        """Discovery funnel steps 1-3: ask purpose, then amount, then recommend from the deterministic
        quantum match. Shared by the current path and the understand path — the ONLY difference is how
        purpose/amount landed in `slots` (keyword extractors vs the understanding signal)."""
        funnel = self._cfg.products["funnel"]
        purpose = slots.get("funnel_purpose")
        amount = slots.get("funnel_amount")

        if purpose is None:
            options = [o["label"] for o in funnel["purpose_options"]]
            reply = funnel["purpose_prompt"]
            return _turn(reply, slots, stage="funnel_purpose",
                         ui={"type": "show_program_options", "payload": {"step": "purpose", "options": options}})

        if amount is None:
            bands = [b["label"] for b in funnel["amount_bands"]]
            reply = funnel["amount_prompt"]
            return _turn(reply, slots, stage="funnel_amount",
                         ui={"type": "show_program_options", "payload": {"step": "amount", "bands": bands}})

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
