"""
The LLM boundary — the ONLY place Vertex/Gemini is touched (brief §3, §7).

The LLM is confined to narrow NLU: intent classification, adversarial detection,
slot extraction, and response phrasing. Nothing here makes a routing or
eligibility decision — those are deterministic (routing.py, rules.py).

Two implementations behind one interface:
  * VertexGeminiClient — real google-genai on Vertex AI (ADC auth, no API keys),
    mirroring services/extraction/app/gemini_client.py.
  * StubLLMClient — deterministic heuristics, zero credentials. Lets the whole
    service, the notebook, and the tests run offline. Selected automatically
    when GCP_PROJECT_ID is unset (LLM_BACKEND=stub).

The Vertex client falls back to the stub on any error so a transient Vertex
failure degrades gracefully instead of 500-ing the turn (the deterministic
guardrail denylist still runs regardless).
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.config.loader import load_config
from app.config.settings import Settings, get_settings
from app.utils.logging import get_logger
from app.utils.prompts import load_prompt, render, system_prompt

log = get_logger("llm")


class LLMClient(ABC):
    """Narrow NLU surface. Returns plain JSON-friendly dicts/strings."""

    @abstractmethod
    def classify_intent(self, message: str, history: list[dict]) -> dict:
        """-> {primary: cat_id|None, confidence: float, secondary: cat_id|None}"""

    @abstractmethod
    def detect_adversarial(self, message: str) -> dict:
        """-> {flagged: bool, category: ADV-xx|None} (LLM stage of the guardrail)"""

    @abstractmethod
    def extract_slots(self, message: str, history: list[dict]) -> dict:
        """-> {slot_key: number|None} for the six Tier-1 eligibility slots"""

    @abstractmethod
    def compose(self, prompt_name: str, *, message: str, history: list[dict],
                fallback: str, **vars: Any) -> str:
        """Phrase a reply from the named prompt + context, grounded in `vars`.
        Returns `fallback` (the agent's deterministic text) if generation is
        unavailable or fails."""

    @abstractmethod
    def rewrite_query(self, message: str, programs: list[tuple[str, str]],
                      history: Optional[list] = None) -> dict:
        """-> {rewritten_query: str, program_code: str|None, program_candidates: [code],
              is_program_dependent: bool}
        Normalise customer vocabulary to bank vocabulary (loan->financing,
        interest->profit rate) and extract an explicitly-named programme (§6a
        branch A). `programs` = [(code, title)] from the live index. With `history`,
        CONDENSE a follow-up ("what about GGSM?", "and the documents?") into a full
        standalone `rewritten_query` using the prior turns (branch B). `program_candidates`
        lists the listed programmes a mistyped / ambiguous name is close to (e.g. "MHIP"
        → [MIHP-I, MHP-I]) when no single one is confident — the advisor asks which."""

    @abstractmethod
    def synthesize_answer(self, query: str, chunks: list, history: Optional[list] = None) -> dict:
        """-> {sentences: [{text: str, cites: [int]}], grounded: bool}
        Write a grounded answer to `query` using ONLY the numbered `chunks`
        (1-based) — each sentence cites the chunk number(s) that support it, and
        nothing unsupported is written. grounded=False when the chunks do not
        answer the question (the caller then abstains / falls back). Phase 1."""

    @abstractmethod
    def generate_clarify(self, message: str, history: list[dict]) -> dict:
        """-> {question: str, options: [{label: str, value: str}]}
        A targeted clarifying question + up to 3 in-scope options for an ambiguous
        SME-financing message (the CLARIFY path). Each option `value` is a natural
        request re-sent through routing when tapped. {"question": "", "options": []}
        when it can't clarify within SME financing — the caller uses its default line."""


# ── Deterministic stub ───────────────────────────────────────────────────────

# Lowercase keyword signals per cat_id. Stub-only heuristics (NOT business
# config — that's intents.yaml). Good enough to route the example bank and to
# run offline; the Vertex model supersedes this when configured.
_KEYWORDS: dict[str, list[str]] = {
    "INS-01": ["talk to a", "speak to a", "speak to someone", "talk to someone", "real person",
               "actual person", "human", "representative", "sales rep", "visit a branch", "branch",
               "contact sales", "call someone"],
    "INS-02": ["program", "programme", "what financing", "financing options", "products", "offer",
               "scheme", "features", "how long does the application", "terms", "what sme financing",
               "get a loan", "get financing", "can i get financing", "get sme financing"],
    "INS-03": ["guideline", "policy", "policies", "shariah", "syariah", "compliant", "compliance",
               "rules for sme"],
    "INS-04": ["eligible", "eligibility", "qualify", "requirements", "criteria",
               "documents do i need", "what documents", "am i eligible"],
    "INS-05": ["want to apply", "apply for", "apply now", "start an application", "start application",
               "new application", "begin the application", "how do i begin", "how do i start", "sign up"],
    "INS-06": ["continue", "draft", "save as draft", "saved my application", "resume my application",
               "resume application"],
    "INS-07": ["status", "track", "where is my application", "check my application", "application status"],
    "OOS-01": ["savings account", "fixed deposit", "current account", "open an account", "deposit rate",
               "credit card"],
    "OOS-02": ["personal loan", "personal financing", "home loan", "mortgage", "car loan", "vehicle loan",
               "education loan", "wedding", "house financing"],
    "OOS-03": ["maybank", "cimb", "rhb", "public bank", "compare", "better than", "other bank",
               "lowest rate", "lowest sme"],
    "OOS-04": ["invest", "stocks", "shares", "buy gold", "buy property", "good time to buy",
               "should i buy", "investment", "property"],
    "OOS-05": ["joke", "how are you", "tell me a", "bored"],
    "OOS-06": ["weather", "news", "what time", "trivia"],
    "OOS-07": ["resume", "homework", "write code", "write my", "assignment"],
    "OOS-08": ["crash", "login", "log in", "app keeps", "website", "not working", "bug", "can't access"],
    "OOS-09": ["rude", "complaint", "officer was", "bad service", "terrible service", "unhappy with"],
    "OOS-10": ["instalment", "installment", "repay", "repayment", "disbursement", "pay my monthly",
               "monthly payment"],
    "ADV-01": ["ignore all previous", "ignore your rules", "forget your rules", "developer mode",
               "you are now"],
    "ADV-02": ["system prompt", "your instructions", "what model are you", "your guardrails",
               "initial instructions"],
    "ADV-03": ["pretend you", "act as a senior", "roleplay", "hypothetically", "senior credit officer"],
    "ADV-04": ["what numbers to enter", "so i get approved", "what to enter", "fake"],
    "ADV-05": ["exact dsr", "exact cutoff", "exact cut-off", "stay under", "threshold"],
    "ADV-06": ["sdn bhd", "another customer", "someone else's"],
    "ADV-07": ["list all", "dump all", "all pending applications", "all records", "database"],
    "ADV-08": ["base64", "decode this"],
    "AMB-02": ["overdraft", "trade facility", "bank guarantee", "letter of credit"],
    "AMB-03": ["need money for my business", "i need funding", "need financing for my business",
               "need money"],
    "AMB-05": ["beer", "alcohol", "liquor", "gambling", "casino", "pork", "nightclub", "conventional loan"],
    # Social pleasantries. Keywords are kept specific (no bare "hi"/"bye") since
    # the stub matches substrings. A greeting bundled with a real request loses
    # on hit-count to the in-scope intent, which is the intended behaviour.
    "SOC-01": ["hello", "assalamualaikum", "salam", "good morning", "good afternoon",
               "good evening", "selamat pagi", "selamat petang", "selamat datang"],
    "SOC-02": ["thank you", "thanks", "terima kasih", "appreciate it", "much appreciated"],
    # Closing / decline. Bare "no"/"ok" are left to the real classifier (context)
    # — as substrings they'd collide ("know", "book"), so the stub keys on phrases.
    "SOC-03": ["no thanks", "no thank you", "that's all", "thats all", "nothing else",
               "i'm good", "im good", "no more", "goodbye", "that's it", "thats it"],
}

_MONEY_RE = re.compile(r"(?:rm\s*)?(\d[\d,]*(?:\.\d+)?)\s*(juta|million|mil|m|k|ribu|thousand)?\b", re.I)
_UNIT_MULT = {"juta": 1e6, "million": 1e6, "mil": 1e6, "m": 1e6, "k": 1e3, "ribu": 1e3, "thousand": 1e3}


def _amount_near(text: str, keywords: list[str], window: int = 45) -> Optional[float]:
    """Find a money value near any keyword. Requires an RM prefix, a magnitude
    unit, a comma, or >=4 digits to count as money (so '3 years' isn't grabbed).
    Scans EVERY occurrence of each keyword (a keyword can appear more than once)."""
    low = text.lower()
    for kw in keywords:
        start = 0
        while True:
            i = low.find(kw, start)
            if i < 0:
                break
            seg = low[max(0, i - window): i + len(kw) + window]
            for m in _MONEY_RE.finditer(seg):
                num, unit = m.group(1), m.group(2)
                looks_money = bool(unit) or "rm" in seg[max(0, m.start() - 3):m.start() + 3] \
                    or "," in num or len(num.replace(",", "").split(".")[0]) >= 4
                if looks_money:
                    return float(num.replace(",", "")) * _UNIT_MULT.get((unit or "").lower(), 1.0)
            start = i + len(kw)
    return None


def _chunk_text(c: Any) -> str:
    """Chunk body from a RetrievalChunk (attr) or a plain dict."""
    return getattr(c, "text", None) or (c.get("text") if isinstance(c, dict) else "") or ""


def _chunk_label(c: Any) -> str:
    """'doc_title · section' for the numbered chunk shown to the synthesiser."""
    md = getattr(c, "metadata", None) or (c.get("metadata") if isinstance(c, dict) else {}) or {}
    return " · ".join(x for x in (md.get("doc_title"), md.get("section")) if x) or "source"


def _first_sentence(text: str, limit: int = 220) -> str:
    """A readable lead sentence: drop the 'LABEL › section › ' breadcrumb Stage 4
    prepends, then take up to the first sentence end (or `limit` chars)."""
    body = text.split(" › ")[-1].strip() if " › " in text else text.strip()
    body = body.replace("\n", " ").strip()
    m = re.search(r"^(.+?[.!?])(\s|$)", body)
    lead = m.group(1) if m else body
    return (lead[:limit].rstrip() + "…") if len(lead) > limit else lead


class StubLLMClient(LLMClient):
    def classify_intent(self, message: str, history: list[dict]) -> dict:
        low = (message or "").lower()
        tax = load_config().taxonomy
        scores: dict[str, int] = {}
        for cat_id, kws in _KEYWORDS.items():
            hits = sum(1 for kw in kws if kw in low)
            if hits:
                scores[cat_id] = hits
        if not scores:
            # Nothing recognised -> low confidence so the router asks R8.
            return {"primary": None, "confidence": 0.3, "secondary": None}

        # Primary = best score, ties broken by taxonomy order.
        order = {r.cat_id: n for n, r in enumerate(tax.rows)}
        primary = sorted(scores, key=lambda c: (-scores[c], order.get(c, 999)))[0]
        best = scores[primary]
        p_type = tax.get(primary).type

        # Secondary = strongest hit of a DIFFERENT type when the primary is
        # in-scope (captures Sheet 9.1 in+out / in+adversarial). Adversarial
        # secondary wins over out-of-scope.
        secondary = None
        if p_type == "in_scope":
            adv = [c for c in scores if tax.get(c).type == "adversarial"]
            oos = [c for c in scores if tax.get(c).type == "out_of_scope"]
            if adv:
                secondary = sorted(adv, key=lambda c: (-scores[c], order.get(c, 999)))[0]
            elif oos:
                secondary = sorted(oos, key=lambda c: (-scores[c], order.get(c, 999)))[0]

        confidence = min(0.5 + 0.2 * min(best, 2), 0.92)
        return {"primary": primary, "confidence": round(confidence, 2), "secondary": secondary}

    def detect_adversarial(self, message: str) -> dict:
        from app.agents.guardrail.denylist import scan
        hit = scan(message)
        if hit:
            return {"flagged": True, "category": hit.category}
        return {"flagged": False, "category": None}

    def extract_slots(self, message: str, history: list[dict]) -> dict:
        # Extract from the CURRENT message only — the eligibility agent persists
        # earlier slots across turns (via client state), so scanning concatenated
        # history would let one slot's number bleed into another's keyword window.
        low = (message or "").lower()
        slots: dict[str, Any] = {}

        m = re.search(r"(\d+)\s*(?:\+)?\s*(?:years?|yrs?|tahun)\b", low)
        if m:
            slots["business_age_years"] = float(m.group(1))
        m = re.search(r"(\d+)\s*(?:staff|employees?|workers?|orang|pekerja)\b", low)
        if m:
            slots["staff_count"] = int(m.group(1))

        for key, kws in {
            "revenue": ["revenue", "turnover", "sales", "annual revenue"],
            "total_equity_or_net_worth": ["net worth", "networth", "equity"],
            "working_capital_limit": ["working capital", "wc limit"],
            "end_balance": ["end balance", "ending balance", "closing balance"],
        }.items():
            val = _amount_near(low, kws)
            if val is not None:
                slots[key] = val
        return slots

    def compose(self, prompt_name: str, *, message: str, history: list[dict],
                fallback: str, **vars: Any) -> str:
        # Offline: the agent's deterministic text IS the reply.
        return fallback

    def rewrite_query(self, message: str, programs: list[tuple[str, str]],
                      history: Optional[list] = None) -> dict:
        text = message or ""
        low = text.lower()
        rewritten = text
        for pat, repl in ((r"\binterest rate\b", "profit rate"), (r"\binterest\b", "profit rate"),
                          (r"\bloans?\b", "financing"), (r"\bborrow\b", "finance")):
            rewritten = re.sub(pat, repl, rewritten, flags=re.I)
        program_code = None
        for code, title in programs:
            if code.lower() in low or (title and title.lower() in low):
                program_code = code
                break
        dep = bool(re.search(r"\b(rate|profit rate|amount|size|how much|tenure|margin|"
                             r"guarantee|eligib|qualify)\b", low))
        return {"rewritten_query": rewritten.strip() or text, "program_code": program_code,
                "program_candidates": [], "is_program_dependent": dep}

    def synthesize_answer(self, query: str, chunks: list, history: Optional[list] = None) -> dict:
        # Offline/deterministic: no real synthesis — surface a lead sentence from
        # each of the top chunks, each citing itself. Enough to render the chip +
        # Sources UI without Vertex; the Vertex client writes the real prose.
        if not chunks:
            return {"sentences": [], "grounded": False}
        sentences = [{"text": _first_sentence(_chunk_text(c)), "cites": [i]}
                     for i, c in enumerate(chunks[:2], start=1)]
        return {"sentences": sentences, "grounded": True}

    def generate_clarify(self, message: str, history: list[dict]) -> dict:
        # Offline: no smart clarify — the caller uses its default R8 line + preset chips.
        return {"question": "", "options": []}


# ── Vertex AI / Gemini ───────────────────────────────────────────────────────

class VertexGeminiClient(LLMClient):
    """google-genai on Vertex AI. Imported lazily so the stub path needs none
    of the GCP packages. Auth is ADC (attached SA on Cloud Run, gcloud ADC
    locally) — no API keys, ever."""

    def __init__(self, settings: Settings):
        if not settings.gcp_project_id:
            raise RuntimeError("LLM_BACKEND=vertex requires GCP_PROJECT_ID.")
        from google import genai  # lazy
        self._settings = settings
        self._genai = genai
        self._client = genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.vertex_location,
        )
        self._model = settings.model_id
        self._fallback = StubLLMClient()

    # -- helpers --
    def _history_text(self, history: list[dict]) -> str:
        lines = [f"{t.get('role', 'user')}: {t.get('content', '')}" for t in (history or [])]
        return "\n".join(lines) if lines else "(no prior turns)"

    def _generate_json(self, prompt_name: str, filled: str, schema: dict) -> dict:
        from google.genai import types
        cfg = types.GenerateContentConfig(
            system_instruction=system_prompt(prompt_name),
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
        )
        resp = self._client.models.generate_content(model=self._model, contents=filled, config=cfg)
        return json.loads(resp.text)

    def _generate_text(self, prompt_name: str, filled: str) -> str:
        from google.genai import types
        cfg = types.GenerateContentConfig(system_instruction=system_prompt(prompt_name), temperature=0.3)
        resp = self._client.models.generate_content(model=self._model, contents=filled, config=cfg)
        return (resp.text or "").strip()

    # -- interface --
    def classify_intent(self, message: str, history: list[dict]) -> dict:
        try:
            tax = load_config().taxonomy
            cat_ids = tax.ids()
            # Group the taxonomy by type so the model sees the families clearly.
            by_type: dict[str, list] = {}
            for r in tax.rows:
                by_type.setdefault(r.type, []).append(r)
            taxonomy = "\n".join(
                f"[{t.upper()}]\n" + "\n".join(f"  {r.cat_id}: {r.category} — {r.definition}" for r in rows)
                for t, rows in by_type.items()
            )
            filled = render(load_prompt("intent_classifier"),
                            taxonomy=taxonomy, history=self._history_text(history), message=message)
            # Constrain primary/secondary to the real cat_id set — the model
            # cannot emit an invalid label. This is the main consistency lever.
            schema = {
                "type": "OBJECT",
                "properties": {
                    "primary": {"type": "STRING", "enum": cat_ids},
                    "confidence": {"type": "NUMBER"},
                    "secondary": {"type": "STRING", "enum": cat_ids, "nullable": True},
                },
                "required": ["primary", "confidence"],
            }
            out = self._generate_json("intent_classifier", filled, schema)
            return {
                "primary": out.get("primary"),
                "confidence": float(out.get("confidence", 0.0)),
                "secondary": out.get("secondary") or None,
            }
        except Exception as exc:  # noqa: BLE001 — degrade to stub, never 500 the turn
            log.warning("Vertex classify_intent failed (%s); using stub.", exc)
            return self._fallback.classify_intent(message, history)

    def detect_adversarial(self, message: str) -> dict:
        try:
            adv_ids = [r.cat_id for r in load_config().taxonomy.of_type("adversarial")]
            filled = render(load_prompt("guardrail"), message=message)
            schema = {
                "type": "OBJECT",
                "properties": {
                    "flagged": {"type": "BOOLEAN"},
                    "category": {"type": "STRING", "enum": adv_ids, "nullable": True},
                },
                "required": ["flagged"],
            }
            out = self._generate_json("guardrail", filled, schema)
            flagged = bool(out.get("flagged"))
            return {"flagged": flagged, "category": (out.get("category") or None) if flagged else None}
        except Exception as exc:  # noqa: BLE001 — fail toward the deterministic denylist
            log.warning("Vertex detect_adversarial failed (%s); using stub.", exc)
            return self._fallback.detect_adversarial(message)

    def extract_slots(self, message: str, history: list[dict]) -> dict:
        try:
            filled = render(load_prompt("eligibility_extraction"),
                            history=self._history_text(history), message=message)
            schema = {
                "type": "OBJECT",
                "properties": {
                    "business_age_years": {"type": "NUMBER", "nullable": True},
                    "total_equity_or_net_worth": {"type": "NUMBER", "nullable": True},
                    "revenue": {"type": "NUMBER", "nullable": True},
                    "working_capital_limit": {"type": "NUMBER", "nullable": True},
                    "end_balance": {"type": "NUMBER", "nullable": True},
                    "staff_count": {"type": "NUMBER", "nullable": True},
                },
            }
            out = self._generate_json("eligibility_extraction", filled, schema)
            return {k: v for k, v in out.items() if v is not None}
        except Exception as exc:  # noqa: BLE001
            log.warning("Vertex extract_slots failed (%s); using stub.", exc)
            return self._fallback.extract_slots(message, history)

    def compose(self, prompt_name: str, *, message: str, history: list[dict],
                fallback: str, **vars: Any) -> str:
        try:
            filled = render(load_prompt(prompt_name),
                            message=message, history=self._history_text(history), **vars)
            text = self._generate_text(prompt_name, filled)
            return text or fallback
        except Exception as exc:  # noqa: BLE001
            log.warning("Vertex compose(%s) failed (%s); using fallback text.", prompt_name, exc)
            return fallback

    def rewrite_query(self, message: str, programs: list[tuple[str, str]],
                      history: Optional[list] = None) -> dict:
        try:
            codes = [c for c, _ in programs]
            listing = "\n".join(f"  {c} — {t}" for c, t in programs) or "  (none configured)"
            filled = render(load_prompt("query_rewrite"), programs=listing, message=message,
                            history=self._history_text(history or []))
            pc_schema = ({"type": "STRING", "enum": codes, "nullable": True} if codes
                         else {"type": "STRING", "nullable": True})
            cand_item = ({"type": "STRING", "enum": codes} if codes else {"type": "STRING"})
            schema = {
                "type": "OBJECT",
                "properties": {
                    "rewritten_query": {"type": "STRING"},
                    "program_code": pc_schema,
                    "program_candidates": {"type": "ARRAY", "items": cand_item},
                    "is_program_dependent": {"type": "BOOLEAN"},
                },
                "required": ["rewritten_query"],
            }
            out = self._generate_json("query_rewrite", filled, schema)
            code_set = set(codes)
            return {
                "rewritten_query": (out.get("rewritten_query") or message).strip(),
                "program_code": out.get("program_code") or None,
                "program_candidates": [c for c in (out.get("program_candidates") or []) if c in code_set],
                "is_program_dependent": bool(out.get("is_program_dependent", False)),
            }
        except Exception as exc:  # noqa: BLE001 — degrade to stub, never break the turn
            log.warning("Vertex rewrite_query failed (%s); using stub.", exc)
            return self._fallback.rewrite_query(message, programs)

    def generate_clarify(self, message: str, history: list[dict]) -> dict:
        try:
            filled = render(load_prompt("clarify"), message=message, history=self._history_text(history))
            schema = {
                "type": "OBJECT",
                "properties": {
                    "question": {"type": "STRING"},
                    "options": {"type": "ARRAY", "items": {
                        "type": "OBJECT",
                        "properties": {"label": {"type": "STRING"}, "value": {"type": "STRING"}},
                        "required": ["label", "value"],
                    }},
                },
                "required": ["question"],
            }
            out = self._generate_json("clarify", filled, schema)
            options = [
                {"label": (o.get("label") or "").strip(), "value": (o.get("value") or "").strip()}
                for o in (out.get("options") or [])
                if (o.get("label") or "").strip() and (o.get("value") or "").strip()
            ]
            return {"question": (out.get("question") or "").strip(), "options": options[:3]}
        except Exception as exc:  # noqa: BLE001 — degrade to the default clarify, never break the turn
            log.warning("Vertex generate_clarify failed (%s); using default clarify.", exc)
            return self._fallback.generate_clarify(message, history)

    def synthesize_answer(self, query: str, chunks: list, history: Optional[list] = None) -> dict:
        n = len(chunks)
        if not n:
            return {"sentences": [], "grounded": False}
        try:
            listing = "\n\n".join(f"[{i}] ({_chunk_label(c)})\n{_chunk_text(c)}"
                                  for i, c in enumerate(chunks, start=1))
            filled = render(load_prompt("answer_synthesis"), query=query, chunks=listing,
                            history=self._history_text(history or []))
            schema = {
                "type": "OBJECT",
                "properties": {
                    "grounded": {"type": "BOOLEAN"},
                    "sentences": {"type": "ARRAY", "items": {
                        "type": "OBJECT",
                        "properties": {
                            "text": {"type": "STRING"},
                            "label": {"type": "STRING"},
                            "bullet": {"type": "BOOLEAN"},
                            "cites": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                        },
                        "required": ["text"],
                    }},
                },
                "required": ["grounded"],
            }
            out = self._generate_json("answer_synthesis", filled, schema)
            sentences = []
            for s in (out.get("sentences") or []):
                text = (s.get("text") or "").strip()
                if not text:
                    continue
                # keep only in-range citation numbers — the model can't invent a source
                cites = [c for c in (s.get("cites") or []) if isinstance(c, int) and 1 <= c <= n]
                entry = {"text": text, "cites": cites}
                # `label` (optional) turns a sentence into a scannable key fact; the lead has none.
                label = (s.get("label") or "").strip()
                if label:
                    entry["label"] = label
                # `bullet` (optional) marks a discrete list item — the UI groups runs of these into
                # a <ul>; the model sets it only when listing parallel items (documents, sectors…).
                if s.get("bullet"):
                    entry["bullet"] = True
                sentences.append(entry)
            grounded = bool(out.get("grounded")) and bool(sentences)
            return {"sentences": sentences, "grounded": grounded}
        except Exception as exc:  # noqa: BLE001 — degrade to stub, never break the turn
            log.warning("Vertex synthesize_answer failed (%s); using stub.", exc)
            return self._fallback.synthesize_answer(query, chunks)


# ── Factory ──────────────────────────────────────────────────────────────────

def get_llm_client(settings: Optional[Settings] = None) -> LLMClient:
    settings = settings or get_settings()
    if settings.llm_backend == "vertex":
        try:
            return VertexGeminiClient(settings)
        except Exception as exc:  # noqa: BLE001 — misconfig shouldn't brick startup
            log.warning("Falling back to StubLLMClient (Vertex init failed: %s).", exc)
            return StubLLMClient()
    return StubLLMClient()
