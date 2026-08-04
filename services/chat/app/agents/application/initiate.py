"""
Initiate new application (brief §7) — Sheet 6.

Detects readiness signals (S1 explicit intent, S2 post-eligibility, S3 asks how
to start, S4 ready after Q&A) and offers to start the application, emitting an
open_application_link action. The URL is a PLACEHOLDER constant (Sheet 6.2 value
is "X", pending from BMMB) sourced from settings.new_application_url.
"""
from __future__ import annotations

from typing import Optional

from app.config.settings import get_settings

_MESSAGE = "Great — I'll take you to the application form to get started."


class Initiate:
    def handle(self, message: str, history: list[dict], *, post_eligibility: bool = False,
               program: Optional[str] = None) -> dict:
        url = get_settings().new_application_url   # placeholder until BMMB supplies the real page
        # Name the programme when we know it (carried from a prior program answer),
        # so "apply for that program" doesn't reset to a generic application form.
        if program:
            reply = f"Great — I'll take you to the application form for {program} to get started."
        elif post_eligibility:
            reply = "Since you've passed the initial check, " + _MESSAGE[0].lower() + _MESSAGE[1:]
        else:
            reply = _MESSAGE
        payload = {"url": url}
        if program:
            payload["program"] = program
        return {
            "reply": reply,
            "stage": "initiate",
            "ui_action": {"type": "open_application_link", "payload": payload},
            "citations": [],
            "handoff": False,
            "handoff_reason": None,
            "decision_inputs": {"signal": "S2" if post_eligibility else "S1/S3",
                                "url_is_placeholder": True, "program": program},
        }
