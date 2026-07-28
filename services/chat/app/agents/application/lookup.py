"""
Continue draft / Track application (brief §7) — Sheets 7 & 8 — PLACEHOLDER.

Sheets 7 and 8 are empty: the real application-stage values and per-stage page
URLs are pending from BMMB. This stubs the stage->redirect mapping behind a
stable shape so nothing downstream breaks and the swap-in is trivial.

CRITICAL-STATE NOTE (§5.1): application stage must be resolved SERVER-SIDE by
application_id, never trusted from client-claimed context.state. The real
implementation will look the id up in Cloud SQL; this stub just demonstrates the
contract.
"""
from __future__ import annotations

from typing import Optional

# TODO: replace with the real stage list (Sheet 7.2 / 8.2) + stage->URL map
# (Sheet 7.3 / 8.3), resolved from Cloud SQL by application_id.
_STAGE_REDIRECT_STUB = {
    "customer_info": "https://apply.muamalat.example/sme/{app_id}/customer-info",
    "document_upload": "https://apply.muamalat.example/sme/{app_id}/documents",
    "review": "https://apply.muamalat.example/sme/{app_id}/review",
    "submitted": "https://track.muamalat.example/sme/{app_id}",
}
_PLACEHOLDER_STAGE = "document_upload"   # stubbed; real value comes from lookup


class ApplicationLookup:
    def handle(self, message: str, history: list[dict], *, application_id: Optional[str],
               mode: str = "continue") -> dict:
        # Need an application id to look anything up.
        if not application_id:
            verb = "continue your saved draft" if mode == "continue" else "track your application"
            return {
                "reply": f"Sure — to {verb}, could you share your application ID?",
                "stage": f"await_application_id_{mode}",
                "ui_action": {"type": "none", "payload": {}},
                "citations": [],
                "handoff": False,
                "handoff_reason": None,
                "decision_inputs": {"mode": mode, "application_id": None},
            }

        # TODO(server-side lookup): resolve the real stage from Cloud SQL by
        # application_id. Stubbed below.
        stage = _PLACEHOLDER_STAGE
        url = _STAGE_REDIRECT_STUB.get(stage, "").format(app_id=application_id)
        if mode == "track":
            reply = f"Your application (ID {application_id}) is currently at the '{stage}' stage. " \
                    "I'll open the tracking page for you."
            ui = {"type": "open_application_link", "payload": {"url": url, "stage": stage, "mode": "track"}}
        else:
            reply = f"Welcome back — your draft (ID {application_id}) is at the '{stage}' stage. " \
                    "Continuing you to the next step now."
            ui = {"type": "open_application_link", "payload": {"url": url, "stage": stage, "mode": "continue"}}

        return {
            "reply": reply,
            "stage": f"{mode}_redirect",
            "ui_action": ui,
            "citations": [],
            "handoff": False,
            "handoff_reason": None,
            "decision_inputs": {"mode": mode, "application_id": application_id,
                                "stage": stage, "stage_is_stub": True},
        }
