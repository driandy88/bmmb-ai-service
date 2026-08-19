"""
Eligibility agent (brief §7) — slot-fill loop around the deterministic rules.

Flow on a FRESH eligibility turn (no slots collected yet):
  1. Detect a genuine Tier-2 signal (config tier2_keywords — CCRIS/DSCR/gearing/
     etc.). If present, the bot must NOT evaluate it — hand off to Sales (T4).
  2. Otherwise, one bounded LLM decision (`decide_eligibility_intent`): does the
     customer want the check STARTED, or are they asking about one or more
     SPECIFIC criteria without wanting to start the flow? This is the only
     thing the LLM decides here — which deterministic thing happens next, never
     a verdict and never a number (the qualitative answer comes from a fixed
     phrase table, not the model's own words).
  3. `answer_criterion` -> a qualitative answer (no exact thresholds — existing
     product stance against eligibility-gaming), no card, no handoff.
  4. `start_check` (the un-freeze, and the safe default) -> launch the card,
     carrying forward anything already mentioned earlier in the SAME
     conversation (`_recall_known_slots`) so the card doesn't re-ask it.

Flow once slots exist (the card's one-shot submission, or a document-initiated
flow already in progress):
  1. LLM extracts the Tier-1 slots (+ the informational operating_profit slot);
     merge with slots already collected.
  2. rules.evaluate() decides (PURE — the LLM never decides; operating_profit is
     never passed to it, so it can never gate the verdict).
  3. If INCOMPLETE, ask for the next missing slot. If a verdict, phrase the
     explanation + indicative-only disclaimer (LLM `compose`, deterministic
     fallback offline), plus the computed working-capital limit on a PASS
     (`limit.py` — deterministic, revenue x a configured pct).

Returns a plain dict the orchestrator node folds into the envelope. The audit
inputs are the RULE OUTCOMES (pass/fail per rule), never the raw financial
figures — those stay in the server-side slot cache (§2.5 PII discipline).
"""
from __future__ import annotations

from typing import Any, Optional

from app.agents.eligibility import document_map, rules
from app.agents.eligibility.limit import compute_working_capital_limit
from app.config.loader import AppConfig, load_config
from app.integrations.llm import LLMClient

_SLOTFILL_STAGE = "eligibility_slotfill"
_DONE_STAGE = "eligibility_done"

# Deterministic, qualitative phrasing per criterion topic for the narrow-question
# path — no numbers, so a customer can't read off the exact bar to clear
# (existing product stance, response R7). Order matches rules.SLOT_KEYS' ask
# order for the "which things we look at" fallback line.
_CRITERIA_TEXT = {
    "business_age_years": "how long your business has been operating",
    "total_equity_or_net_worth": "your total equity or net worth",
    "revenue": "your annual revenue or turnover",
    "end_balance": "your average bank end balance over the last 6 months",
    "staff_count": "how many staff you employ",
    "operating_profit": "your operating profit (this one's informational only — it doesn't affect the check)",
}


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

    def _qualitative_answer(self, topics: list[str]) -> str:
        """Deterministic phrasing for a narrow criteria question — the model only
        ever decided WHICH topics were asked about (decide_eligibility_intent);
        the words themselves come from the fixed table above, never the model."""
        labels = [_CRITERIA_TEXT[t] for t in topics if t in _CRITERIA_TEXT]
        if not labels:
            listed = ", ".join(_CRITERIA_TEXT[k] for k in rules.SLOT_KEYS)
            body = f"We look at a few things as part of an indicative check — {listed}."
        elif len(labels) == 1:
            body = f"That's one of the things we look at — {labels[0]}."
        else:
            body = "Those are both things we look at — " + " and ".join(labels) + "."
        return body + " Want me to run the quick check? I'll ask for a few details."

    def _recall_known_slots(self, history: list[dict]) -> dict:
        """Best-effort pre-fill for the card: scan each PAST customer turn
        independently (never the whole history as one blob — extract_slots'
        stub relies on a narrow keyword window per call, and concatenating
        turns risks one slot's number bleeding into another's window). Nothing
        found here is trusted for the verdict — the card submission still runs
        through the same extract + evaluate path as any other answer."""
        known: dict[str, Any] = {}
        for turn in history or []:
            if (turn.get("role") or "").lower() not in ("user", "customer"):
                continue
            text = (turn.get("content") or "").strip()
            if not text:
                continue
            for k, v in (self._llm.extract_slots(text, []) or {}).items():
                if v is not None:
                    known[k] = v
        return known

    def handle(self, message: str, history: list[dict], collected_slots: dict) -> dict:
        slots_in = collected_slots or {}
        has_prior_slots = any(slots_in.get(k) is not None for k in rules.SLOT_KEYS)

        if has_prior_slots:
            # Continuing an in-progress flow (document-initiated, or already
            # mid-slotfill via a prior turn): extract + merge + respond, unchanged.
            extracted = self._llm.extract_slots(message, history) or {}
            slots = {**slots_in}
            for k in (*rules.SLOT_KEYS, "operating_profit"):
                if extracted.get(k) is not None:
                    slots[k] = extracted[k]
            return self._respond(slots)

        # Nothing collected yet. A genuine Tier-2 signal hands off immediately,
        # ahead of everything else — no LLM call needed, and it must win even if
        # the same message happens to also contain an extractable figure.
        if self._tier2_signal(message, history):
            return {
                "reply": "",                   # sales_handoff composes the reply
                "slots": dict(slots_in),
                "status": rules.REFER_TO_SALES,
                "stage": _DONE_STAGE,
                "missing": [],
                "ui_action": {"type": "none", "payload": {}},
                "handoff": True,
                "handoff_reason": "T4",
                "decision_inputs": {"rule_version": self._cfg.rule_version, "status": rules.REFER_TO_SALES,
                                    "eligibility": "tier2_signal"},
                "citations": [],
            }

        # The card submits everything as ONE combined message, with no prior
        # round trip -- so `collected_slots` is still empty on that submission
        # too, same as a genuinely fresh question. Try extracting from THIS
        # message before deciding it's a fresh question: if it already carries
        # Tier-1 figures, go straight to the verdict like any other turn would.
        extracted = self._llm.extract_slots(message, history) or {}
        if any(extracted.get(k) is not None for k in rules.SLOT_KEYS):
            slots = {**slots_in}
            for k in (*rules.SLOT_KEYS, "operating_profit"):
                if extracted.get(k) is not None:
                    slots[k] = extracted[k]
            return self._respond(slots)

        # Genuinely nothing on file and nothing in this message either -- decide
        # whether the customer wants the check started, or is asking about a
        # specific criterion without wanting to start it.
        decision = self._llm.decide_eligibility_intent(message, history) or {}
        if decision.get("action") == "answer_criterion" and decision.get("topics"):
            topics = list(decision["topics"])
            return {
                "reply": self._qualitative_answer(topics),
                "slots": dict(slots_in),
                "status": None,
                "stage": None,
                "missing": [],
                "ui_action": {"type": "none", "payload": {}},
                "handoff": False,
                "handoff_reason": None,
                "decision_inputs": {"eligibility": "answered_criterion", "topics": topics},
                "citations": [],
            }

        # start_check (also the safe default for an unmatched/ambiguous decision):
        # the un-freeze — launch the card, carrying forward anything the customer
        # already mentioned earlier in this same conversation.
        known = self._recall_known_slots(history)
        return {
            "reply": "",
            "slots": dict(slots_in),
            "status": rules.INCOMPLETE,
            "stage": _SLOTFILL_STAGE,
            "missing": list(rules.SLOT_KEYS),
            "ui_action": {"type": "render_eligibility_form",
                          "payload": {"next_slot": rules.SLOT_KEYS[0],
                                      "question": self._slot_prompt(rules.SLOT_KEYS[0]),
                                      "options": self._slot_options(rules.SLOT_KEYS[0]),
                                      "collected": [],
                                      "known_slots": known}},
            "handoff": False,
            "handoff_reason": None,
            "decision_inputs": {"eligibility": "started"},
            "citations": [],
        }

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
        # Informational only — never in rule_results, never influences result.status.
        if slots.get("operating_profit") is not None:
            decision_inputs["operating_profit_provided"] = True

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
                                          "collected": list(slots.keys()),
                                          "known_slots": {k: v for k, v in slots.items() if v is not None}}},
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
        payload: dict[str, Any] = {"outcome": "PASS" if passed else "FAIL", "options": options}
        # The limit is a computed OUTPUT (limit.py — deterministic, revenue x a
        # configured pct), shown only on a PASS: on a FAIL there's no figure to
        # hand back, and showing one would read as an offer it isn't.
        if passed:
            limit = compute_working_capital_limit(
                slots.get("revenue"), self._cfg.eligibility.get("working_capital_limit_pct", 0.30),
            )
            if limit is not None:
                payload["working_capital_limit"] = limit
        return {
            "reply": reply,
            "slots": slots,
            "status": result.status,
            "stage": _DONE_STAGE,
            "missing": [],
            "ui_action": {"type": "show_eligibility_result", "payload": payload},
            "handoff": False,
            "handoff_reason": None,
            "decision_inputs": decision_inputs,
            "citations": [],
        }
