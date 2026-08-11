"""
Sales handoff agent (brief §7) — Sheet 2. A 2-turn flow:

  Turn 1 (_start): any sales trigger -> a canned intro (handoff_intro) + a location
    IntakeCard (ui_action render_contact_form), stage = await_contact_location.
  Turn 2 (_resolve): the customer's state/city -> region -> the correct Sales
    contact + a contact card. Unresolvable / skipped -> the Overall team (R1).

Handoff triggers: T1 complaint/dispute · T2 explicit request for human · T3
repeated bot failure (low-confidence loop) · T4 sensitive/borderline eligibility
(also the FROZEN eligibility funnel). The trigger is supplied by the orchestrator
(T3 from routing, T4 from the eligibility agent) or inferred from the message
(T1/T2); it only shapes the turn-1 intro — turn 2 just gives the regional contact.
"""
from __future__ import annotations

import random
from typing import Optional

from ...config.loader import AppConfig, load_config
from ...config.settings import get_settings

# Stage the client echoes back after turn 1 so turn 2 resolves the contact.
_AWAIT_LOCATION_STAGE = "await_contact_location"


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
        # Regions with more than one rep (e.g. Central, Southern, East Coast) —
        # pick one at random so the load isn't always on contacts[0].
        c = dict(random.choice(contacts))
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

    # -- main (2-turn: start -> collect location -> resolve contact) --------
    def handle(self, message: str, history: list[dict], *, reason: Optional[str] = None,
               channel: str = "customer", stage: Optional[str] = None) -> dict:
        """Turn 1 (any sales trigger): a canned intro + a location IntakeCard.
        Turn 2 (stage == await_contact_location): resolve the state/city the
        customer gave -> the right regional contact (unresolvable -> R1)."""
        if stage == _AWAIT_LOCATION_STAGE:
            return self._resolve(message, history)
        return self._start(message, reason)

    def _start(self, message: str, reason: Optional[str]) -> dict:
        trigger = reason
        if trigger not in ("T1", "T2", "T3", "T4"):
            detected = self.detect_triggers(message)
            trigger = detected[0] if detected else "T2"   # default: general handoff
        intro = self._sales.get("handoff_intro", {})
        reply = intro.get(trigger) or intro.get("default") or "I'll connect you with our SME financing team."
        trigger_name = next((t["name"] for t in self._sales["triggers"] if t["id"] == trigger), trigger)
        return {
            "reply": reply,
            "stage": _AWAIT_LOCATION_STAGE,
            "ui_action": {"type": "render_contact_form", "payload": {}},
            "citations": [],
            "handoff": True,
            "handoff_reason": trigger,
            "handoff_block": {"required": True, "reason": f"{trigger}: {trigger_name}", "contact": None},
            "decision_inputs": {"trigger": trigger, "phase": "collect_location"},
        }

    def _resolve(self, message: str, history: list[dict]) -> dict:
        # Resolve from the customer's actual answer only (history is kept in the
        # signature but intentionally unused). Searching the whole transcript let
        # incidental place mentions from earlier turns (usually KL / Selangor)
        # win the longest-name match, so every pick resolved to Central; the
        # turn-2 message already carries the location they chose.
        region = self.resolve_region(message)
        contact = self.contact_for(region["region_id"])
        who = self._branch_contact_str(contact)
        hours = contact.get("hours", "")
        if region["matched"]:
            reply = (f"You're in our {region['region']} region — here's the Bank Muamalat SME financing "
                     f"contact for your area: {who}. They're available {hours}.")
        else:
            reply = (f"No problem — our SME financing team can help: {who}. They're available {hours}.")
        return {
            "reply": reply,
            "stage": "handoff",
            "ui_action": {"type": "show_contact_card", "payload": contact},
            "citations": [],
            "handoff": True,
            "handoff_reason": "T2",
            "handoff_block": {"required": True, "reason": "resolved contact", "contact": contact},
            "decision_inputs": {"region_id": region["region_id"], "region": region["region"],
                                "matched_location": region["matched"], "phase": "resolved"},
        }
