"""
Eligibility agent (brief §7) — slot-fill loop around the deterministic rules.

Flow each turn:
  1. Detect a Tier-2 signal in the message (config tier2_keywords). If present,
     the bot must NOT evaluate it — hand off to Sales (T4).
  2. LLM extracts the six Tier-1 slots; merge with slots already collected.
  3. rules.evaluate() decides (PURE — the LLM never decides).
  4. If INCOMPLETE, ask for the next missing slot. If a verdict, phrase the
     explanation + indicative-only disclaimer (LLM `compose`, deterministic
     fallback offline).

Returns a plain dict the orchestrator node folds into the envelope. The audit
inputs are the RULE OUTCOMES (pass/fail per rule), never the raw financial
figures — those stay in the server-side slot cache (§2.5 PII discipline).
"""
from __future__ import annotations

from typing import Any, Optional

from app.agents.eligibility import document_map, rules
from app.config.loader import AppConfig, load_config
from app.integrations.llm import LLMClient

_SLOTFILL_STAGE = "eligibility_slotfill"
_DONE_STAGE = "eligibility_done"


class EligibilityAgent:
    def __init__(self, llm: LLMClient, config: Optional[AppConfig] = None):
        self._llm = llm
        self._cfg = config or load_config()

    # -- Tier-2 detection --------------------------------------------------
    def _tier2_signal(self, message: str, history: list[dict]) -> bool:
        text = " ".join(t.get("content", "") for t in (history or [])) + " " + (message or "")
        low = text.lower()
        return any(kw in low for kw in self._cfg.eligibility.get("tier2_keywords", []))

    def _slot_prompt(self, key: str) -> str:
        for rule in self._cfg.eligibility["tier1"]:
            if rule["key"] == key:
                return rule.get("prompt", f"Could you share your {rule['label'].lower()}?")
        return "Could you share a bit more so I can check?"

    def _slot_options(self, key: str) -> list:
        """Quick-reply answers for this slot (config-driven); [] if none."""
        for rule in self._cfg.eligibility["tier1"]:
            if rule["key"] == key:
                return list(rule.get("options", []))
        return []

    # -- fallback (offline) verdict phrasing -------------------------------
    def _explain(self, result: rules.EligibilityResult) -> str:
        d = result.disclaimer
        if result.status == rules.INDICATIVE_ELIGIBLE:
            return (
                "Good news — based on what you've shared, you meet our initial SME "
                "financing criteria. The next step is a full review by our SME financing "
                f"team. {d}"
            )
        # NOT_ELIGIBLE — explain which criteria weren't met (criteria are public).
        reasons = []
        for c in result.checks:
            if c.ok is False:
                if c.bound_min is not None and c.value is not None and c.value < c.bound_min:
                    reasons.append(f"{c.label.lower()} needs to be at least {c.bound_min:g} (you noted {c.value:g})")
                elif c.bound_max is not None and c.value is not None and c.value > c.bound_max:
                    reasons.append(f"{c.label.lower()} looks higher than our indicative limit of {c.bound_max:g}")
        reason_text = "; ".join(reasons) if reasons else "some initial criteria weren't met"
        return (
            "Based on what you've shared, it looks like you may not meet some of our "
            f"initial criteria yet: {reason_text}. {d} Our SME financing team can talk "
            "through your options."
        )

    def handle(self, message: str, history: list[dict], collected_slots: dict) -> dict:
        slots_in = collected_slots or {}
        # FROZEN typed funnel: a FRESH eligibility question (no slots collected yet)
        # routes to the sales-contact flow (T4) — the SME team does the assessment.
        # A flow ALREADY in progress with slots is only reachable via the document-
        # upload path, which is kept: let it finish its verdict rather than drop it.
        has_slots = any(slots_in.get(k) is not None for k in rules.SLOT_KEYS)
        if not has_slots:
            return {
                "reply": "",                   # sales_handoff composes the reply
                "slots": dict(slots_in),
                "status": rules.REFER_TO_SALES,
                "stage": _DONE_STAGE,
                "missing": [],
                "ui_action": {"type": "none", "payload": {}},
                "handoff": True,
                "handoff_reason": "T4",        # sensitive/borderline eligibility -> Sales
                "decision_inputs": {"rule_version": self._cfg.rule_version, "status": rules.REFER_TO_SALES,
                                    "eligibility": "frozen"},
                "citations": [],
            }

        # Document-initiated flow in progress: extract + merge + deterministic verdict.
        extracted = self._llm.extract_slots(message, history) or {}
        slots = {**slots_in}
        for k in rules.SLOT_KEYS:
            if extracted.get(k) is not None:
                slots[k] = extracted[k]
        return self._respond(slots)

    def ingest_document(
        self,
        template_id: str,
        extracted_data: dict,
        collected_slots: dict,
        *,
        today=None,
    ) -> dict:
        """Fill Tier-1 slots from an uploaded document (via the extraction
        service), merge with what's collected, then run the SAME evaluate →
        slot-fill / verdict flow as a typed turn. Only Tier-1 figures are read;
        Tier-2 fields in the document are ignored (document_map.py)."""
        mapped = document_map.map_document_to_slots(template_id, extracted_data or {}, today=today)
        slots = {**(collected_slots or {})}
        for k in rules.SLOT_KEYS:
            if mapped.get(k) is not None:
                slots[k] = mapped[k]
        res = self._respond(slots)
        res["filled_from_document"] = sorted(mapped.keys())   # which slots the doc provided
        return res

    def _respond(self, slots: dict) -> dict:
        """Deterministic evaluate → INCOMPLETE (ask next slot) or verdict. Shared
        by the typed-chat and document-upload paths so both use identical rules
        and response shapes."""
        result = rules.evaluate(slots, config=self._cfg)

        decision_inputs = {
            "rule_version": result.rule_version,
            "status": result.status,
            "rule_results": {c.key: c.ok for c in result.checks},   # outcomes only, no raw figures
            "missing": result.missing,
            "failed": result.failed,
            "tier2_signal": False,
        }

        # Incomplete -> ask for the next missing slot.
        if result.status == rules.INCOMPLETE:
            return {
                "reply": self._slot_prompt(result.next_missing_slot),
                "slots": slots,
                "status": result.status,
                "stage": _SLOTFILL_STAGE,
                "missing": result.missing,
                "ui_action": {"type": "render_eligibility_form",
                              "payload": {"next_slot": result.next_missing_slot,
                                          "question": self._slot_prompt(result.next_missing_slot),
                                          "options": self._slot_options(result.next_missing_slot),
                                          "collected": list(slots.keys())}},
                "handoff": False,
                "handoff_reason": None,
                "decision_inputs": decision_inputs,
                "citations": [],
            }

        # Verdict -> DETERMINISTIC explanation + disclaimer. Kept out of the LLM
        # on purpose: the indicative-only disclaimer and the verdict wording are
        # compliance-sensitive and must be reviewable/verbatim, not rephrased.
        reply = self._explain(result)
        # Next-step buttons for the frontend `eligResult` step (config-driven).
        passed = result.status == rules.INDICATIVE_ELIGIBLE
        actions = self._cfg.eligibility.get("result_actions", {})
        options = list(actions.get("eligible" if passed else "not_eligible", []))
        return {
            "reply": reply,
            "slots": slots,
            "status": result.status,
            "stage": _DONE_STAGE,
            "missing": [],
            "ui_action": {"type": "show_eligibility_result",
                          "payload": {"outcome": "PASS" if passed else "FAIL", "options": options}},
            "handoff": False,
            "handoff_reason": None,
            "decision_inputs": decision_inputs,
            "citations": [],
        }
