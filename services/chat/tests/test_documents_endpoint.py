"""
POST /chat/documents end-to-end on the stub extraction backend.

Proves the wiring: upload → extraction (stub) → map → eligibility flow → envelope,
including the incremental slot-fill across two uploads and the final verdict.
"""
import io

from fastapi.testclient import TestClient

from services.chat.api import app

client = TestClient(app)


def _upload(template_id, collected_slots="{}"):
    files = {"files": ("doc.pdf", io.BytesIO(b"%PDF-1.4 stub"), "application/pdf")}
    data = {"template_id": template_id, "collected_slots": collected_slots}
    return client.post("/chat/documents", data=data, files=files)


def test_single_upload_fills_slots_and_asks_for_rest():
    r = _upload("audited_financial_statements")
    assert r.status_code == 200
    d = r.json()
    # AFS gives revenue + equity; still incomplete → asks for a missing slot.
    assert d["audit"]["action"] == "dispatch"
    assert d["audit"]["route"].startswith("5.0 In-principle eligibility")
    slots = d["state"]["collected_slots"]
    assert slots["revenue"] == 4_820_500.0 and slots["total_equity_or_net_worth"] == 1_060_425.0
    assert d["ui_action"]["type"] == "render_eligibility_form"    # INCOMPLETE
    assert d["audit"]["decision_inputs"]["source"] == "document"


def test_documents_then_typed_staff_reaches_verdict():
    import json
    # Upload all three financial docs, accumulating slots via collected_slots.
    slots = {}
    for tid in ("business_registration_ssm", "customer_information_details",
                "audited_financial_statements", "bank_statements"):
        d = _upload(tid, json.dumps(slots)).json()
        slots = d["state"]["collected_slots"]
    # Docs fill 5 of 6; staff_count is never in a document.
    assert set(slots) >= {"business_age_years", "working_capital_limit", "revenue",
                          "total_equity_or_net_worth", "end_balance"}
    assert "staff_count" not in slots
    # Add staff via the normal typed /chat path → verdict.
    body = {"message": "we have 8 staff",
            "context": {"state": {"stage": "eligibility_slotfill", "collected_slots": slots}}}
    final = client.post("/chat", json=body).json()
    assert final["ui_action"]["type"] == "show_eligibility_result"
    assert final["ui_action"]["payload"]["outcome"] == "PASS"


def test_rejects_non_document_mime():
    files = {"files": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    r = client.post("/chat/documents", data={"template_id": "bank_statements"}, files=files)
    assert r.status_code == 400
