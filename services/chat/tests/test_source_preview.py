"""Citation → source preview (Tier 1): the allowlist + access-tier gate + URL modes.

Signing itself needs GCP and can't run offline; these cover the deterministic logic — which docs
are servable, the internal-tier block, and the signed→proxy degrade — plus the endpoint's gating."""
import types

import pytest
from fastapi.testclient import TestClient

from app.integrations.source_preview import SourcePreview
from app.main import app


def _sp(mode="off"):
    return SourcePreview(types.SimpleNamespace(source_preview_mode=mode, source_url_ttl_seconds=900))


def test_allowlist_resolves_the_five_kits():
    sp = _sp()
    assert sp.resolve("ggsm3").source_uri.endswith("SalesKit_GGSM3.pdf")
    assert sp.resolve("mihp_i").access_tier == "customer"
    assert sp.resolve("not-a-doc") is None


def test_internal_doc_blocked_on_customer_channel():
    sp = _sp()
    internal = sp.resolve("commercial_financing_internal_criteria")
    assert internal.access_tier == "internal"
    assert sp.allowed(internal, "customer") is False          # never to a customer
    assert sp.allowed(internal, "internal") is True
    assert sp.allowed(sp.resolve("ggsm3"), "customer") is True  # a kit is fine


def test_off_mode_yields_no_url():
    assert _sp("off").url_for(_sp().resolve("ggsm3"), "customer") is None


def test_proxy_mode_returns_stream_path():
    sp = _sp("proxy")
    assert sp.url_for(sp.resolve("ggsm3"), "customer") == "/chat/source/raw?doc_id=ggsm3&channel=customer"


def test_signed_mode_signs_then_degrades_to_proxy(monkeypatch):
    sp = _sp("signed")
    monkeypatch.setattr(SourcePreview, "_signed_url", lambda self, uri: f"https://signed/{uri}")
    assert sp.url_for(sp.resolve("ggsm3"), "customer").startswith("https://signed/")

    def _boom(self, uri):
        raise RuntimeError("no signBlob permission")

    monkeypatch.setattr(SourcePreview, "_signed_url", _boom)  # signing not permitted → proxy fallback
    assert sp.url_for(sp.resolve("ggsm3"), "customer") == "/chat/source/raw?doc_id=ggsm3&channel=customer"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_endpoint_unknown_doc_is_404(client):
    assert client.get("/chat/source", params={"doc_id": "nope"}).status_code == 404


def test_endpoint_internal_doc_forbidden_on_customer(client):
    r = client.get("/chat/source",
                   params={"doc_id": "commercial_financing_internal_criteria", "channel": "customer"})
    assert r.status_code == 403


def test_endpoint_off_mode_is_503(client):
    # Test env has no GCP project → SOURCE_PREVIEW_MODE defaults to "off" → preview unavailable.
    assert client.get("/chat/source", params={"doc_id": "ggsm3"}).status_code == 503
