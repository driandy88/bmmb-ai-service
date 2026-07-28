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
    "SOC-02": ["thank you", "thanks", "terima kasih", "appreciate it", "much appreciated",
               "that's all for now", "goodbye"],
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
