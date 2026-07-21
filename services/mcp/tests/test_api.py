"""Hermetic tests for the MCP service -- no network, no LLM, no email sent.

The one send path is exercised only for its *guard* (missing config -> 503) and
its request validation (422); actually composing/sending is an integration
concern that needs real credentials + a live Gemini/Gmail, so it's not unit
tested here.
"""
import pytest
from fastapi.testclient import TestClient

from services.mcp.agent import normalize_cc
from services.mcp.api import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_catalog_lists_gmail_with_expected_fields():
    servers = client.get("/mcp/servers").json()
    assert [s["key"] for s in servers] == ["gmail"]
    required_by_name = {f["name"]: f["required"] for f in servers[0]["fields"]}
    assert required_by_name == {"to": True, "cc": False, "subject": True, "content_about": True}


@pytest.mark.parametrize("value,expected", [
    (None, ""),
    ("", ""),
    ("a@x.com", "a@x.com"),
    ("a@x.com, b@y.com ", "a@x.com, b@y.com"),
    (["a@x.com", " b@y.com "], "a@x.com, b@y.com"),
    ([None, "", "c@z.com"], "c@z.com"),
])
def test_normalize_cc(value, expected):
    assert normalize_cc(value) == expected


def test_send_missing_config_returns_503(monkeypatch):
    for key in ("SENDER_EMAIL", "APP_PASSWORD", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    r = client.post("/mcp/gmail/send", json={"to": "x@y.com", "subject": "s", "content_about": "hi"})
    assert r.status_code == 503
    assert "Missing environment variables" in r.json()["detail"]


def test_send_missing_required_field_returns_422():
    r = client.post("/mcp/gmail/send", json={"subject": "s", "content_about": "hi"})  # no `to`
    assert r.status_code == 422


def test_catalog_exposes_default_prompt_with_signoff():
    # The UI prefills its "customize prompt" box from this; the default always signs off.
    prompt = client.get("/mcp/servers").json()[0]["system_prompt"]
    assert prompt and "BMMB MCP Email Agent" in prompt


def test_send_accepts_optional_system_prompt(monkeypatch):
    # The override is accepted (not a 422); still guarded by config here.
    for key in ("SENDER_EMAIL", "APP_PASSWORD", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    r = client.post("/mcp/gmail/send", json={
        "to": "x@y.com", "subject": "s", "content_about": "hi", "system_prompt": "Write tersely.",
    })
    assert r.status_code == 503
