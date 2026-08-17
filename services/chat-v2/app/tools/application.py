"""
`start_application` and `lookup_application`.

Both are thin, and both stay thin on purpose: the application URL is still a
placeholder pending BMMB, and the stage lookup is still stubbed — same state as
v1. Vendoring the stub rather than inventing a real one keeps the A/B honest; if
v2 appeared to track applications better it would only be because it was making
things up.

CRITICAL: application stage must be resolved server-side from an application_id
the customer supplies. Never from anything the model inferred, and never for an
id the customer did not give.
"""
from __future__ import annotations

from langchain_core.tools import tool

from app.config.settings import get_settings
from app.runtime.context import current

# Same stub as v1's lookup.py — real stage list and per-stage URLs pending
# BMMB (workbook sheets 7 and 8 are empty).
_STAGE_REDIRECT_STUB = {
    "customer_info": "https://apply.muamalat.example/sme/{app_id}/customer-info",
    "document_upload": "https://apply.muamalat.example/sme/{app_id}/documents",
    "review": "https://apply.muamalat.example/sme/{app_id}/review",
    "submitted": "https://track.muamalat.example/sme/{app_id}",
}
_PLACEHOLDER_STAGE = "document_upload"


@tool
def start_application(programme: str = "") -> str:
    """Open the SME financing application form for the customer.

    Call this when the customer says they are ready to apply. You are opening a
    form, not submitting anything and not approving anything.

    Args:
        programme: the programme they want to apply for, if you have been
            discussing one (e.g. "GGSM Madani"). Pass it so the form carries the
            programme through instead of starting generic.
    """
    ctx = current()
    url = get_settings().new_application_url
    code = (programme or "").strip()

    payload = {"url": url}
    if code:
        payload["program"] = code
    ctx.set_ui("open_application_link", **payload)
    ctx.record("start_application", programme=code or None)

    named = f" for {code}" if code else ""
    return (
        f"Application form{named} is opening for the customer. Confirm it warmly in "
        "one sentence. Do not promise approval or imply the application is submitted."
    )


@tool
def lookup_application(application_id: str, mode: str = "track") -> str:
    """Look up an existing application by its ID, to continue a draft or track status.

    Requires an application ID from the customer — ask for it if you do not have
    one. You cannot look up an application any other way, and you must never look
    up an ID the customer did not give you in this conversation.

    Args:
        application_id: the customer's application ID, exactly as they gave it.
        mode: "continue" to resume an unfinished draft, "track" to check status.
    """
    ctx = current()
    app_id = (application_id or "").strip()
    mode = mode if mode in ("continue", "track") else "track"

    if not app_id:
        ctx.record("lookup_application", result="missing_id")
        return "MISSING_ID: ask the customer for their application ID before looking anything up."

    stage = _PLACEHOLDER_STAGE
    url = _STAGE_REDIRECT_STUB.get(stage, "").format(app_id=app_id)

    ctx.set_ui("open_application_link", url=url, stage=stage, mode=mode)
    ctx.record("lookup_application", mode=mode, stage=stage, stage_is_stub=True)

    verb = "draft" if mode == "continue" else "application"
    return (
        f"Application {app_id} is at the '{stage}' stage. The {verb} page is opening "
        "for the customer. Note this is placeholder stage data pending the real "
        "lookup — state the stage plainly and do not elaborate on what it means."
    )