"""
Sales handoff agent (brief §7) — Sheet 2.

Resolves the customer's state/city -> region -> the correct Sales contact, and
emits the handoff message (H1–H4) + a contact card. Handoff triggers:
  T1 complaint/dispute · T2 explicit request for human · T3 repeated bot failure
  (low-confidence loop) · T4 sensitive/borderline eligibility (Tier-2).

The trigger is usually supplied by the orchestrator (T3 from routing, T4 from
the eligibility agent); when absent it's inferred from the message (T1/T2). An
unresolvable location falls back to the Overall team (R1).
"""
from __future__ import annotations

from typing import Optional

from app.config.loader import AppConfig, load_config
from app.config.settings import get_settings


class SalesHandoff:
    def __init__(self, config: Optional[AppConfig] = None):
        self._cfg = config or load_config()
        self._sales = self._cfg.sales
        # Build lowercase lookup indexes once.
        self._state_index: dict[str, str] = {}   # state name -> region_id
        self._city_index: dict[str, tuple[str, str]] = {}  # city -> (region_id, state)
        for state, entry in self._sales["geo"].items():
            rid = entry["region_id"]
            self._state_index[state.lower()] = rid
            for city in entry.get("cities", []):
                self._city_index[city.lower()] = (rid, state)

    # -- geo resolution ----------------------------------------------------
    def resolve_region(self, text: str) -> dict:
        """Match the text against state names first, then cities. Returns
        {region_id, region, matched} — falls back to R1 if unidentifiable."""
        low = (text or "").lower()
        # States (match longer names first to avoid partials).
        for state in sorted(self._state_index, key=len, reverse=True):
            if state in low:
                rid = self._state_index[state]
                return {"region_id": rid, "region": self._sales["regions"][rid]["region"],
                        "matched": state.title()}
        for city in sorted(self._city_index, key=len, reverse=True):
            if city in low:
                rid, state = self._city_index[city]
                return {"region_id": rid, "region": self._sales["regions"][rid]["region"],
                        "matched": city.title()}
        rid = self._sales.get("fallback_region_id", "R1")
        return {"region_id": rid, "region": self._sales["regions"][rid]["region"], "matched": None}

    def contact_for(self, region_id: str) -> dict:
        contacts = self._sales["regions"][region_id]["contacts"]
        c = dict(contacts[0])
        c["region"] = self._sales["regions"][region_id]["region"]
        c["hours"] = get_settings().handoff_hours
        return c

    # -- trigger detection -------------------------------------------------
    def detect_triggers(self, message: str) -> list[str]:
        low = (message or "").lower()
        fired = []
        if any(w in low for w in ["complaint", "dispute", "rude", "unhappy", "bad service",
                                  "terrible", "poor service"]):
            fired.append("T1")
        if any(w in low for w in ["talk to a", "speak to", "real person", "actual person",
                                  "human", "representative", "someone", "sales rep", "an agent"]):
            fired.append("T2")
        return fired

    def _branch_contact_str(self, contact: dict) -> str:
        parts = [contact.get("employee", "our SME financing team")]
        reach = [x for x in (contact.get("phone"), contact.get("email")) if x]
        if reach:
            parts.append(f"({', '.join(reach)})")
        return " ".join(parts)

    # -- main --------------------------------------------------------------
    def handle(self, message: str, history: list[dict], *, reason: Optional[str] = None,
               channel: str = "customer") -> dict:
        trigger = reason
        if trigger not in ("T1", "T2", "T3", "T4"):
            detected = self.detect_triggers(message)
            trigger = detected[0] if detected else "T2"   # default: general handoff

        region = self.resolve_region(message + " " + " ".join(t.get("content", "") for t in (history or [])))
        contact = self.contact_for(region["region_id"])

        msg_ref = self._sales["trigger_message"].get(trigger, "H1")
        template = self._sales["handoff_messages"][msg_ref]["message"]
        reply = template.format(branch_contact=self._branch_contact_str(contact),
                                hours=contact.get("hours", ""))

        trigger_name = next((t["name"] for t in self._sales["triggers"] if t["id"] == trigger), trigger)
        return {
            "reply": reply,
            "stage": "handoff",
            "ui_action": {"type": "show_contact_card", "payload": contact},
            "citations": [],
            "handoff": True,
            "handoff_reason": trigger,
            "handoff_block": {
                "required": True,
                "reason": f"{trigger}: {trigger_name}",
                "contact": contact,
            },
            "decision_inputs": {"trigger": trigger, "region_id": region["region_id"],
                                "region": region["region"], "matched_location": region["matched"]},
        }
