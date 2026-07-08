"""
Tests for the bbox-generator FastAPI router. align_fields itself is unit
tested in test_bbox_aligner.py; here we only verify the HTTP layer (request
parsing, error handling, response passthrough) by monkeypatching align_fields.
"""
import json

import pytest
from fastapi.testclient import TestClient

from services.bbox_generator.api import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_align_success(client, monkeypatch):
    def fake_align_fields(extracted, field_types, doc_path):
        return {k: {"value": v, "bbox": {"page": 1, "x0": 0, "y0": 0, "x1": 1, "y1": 1},
                    "match_quality": "exact"} for k, v in extracted.items()}

    import services.bbox_generator.api as mod
    monkeypatch.setattr(mod, "align_fields", fake_align_fields)

    resp = client.post(
        "/align",
        files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={
            "extracted": json.dumps({"business_name": "Acme Sdn Bhd"}),
            "field_types": json.dumps({"business_name": "string"}),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["business_name"]["value"] == "Acme Sdn Bhd"
    assert body["business_name"]["match_quality"] == "exact"


def test_align_invalid_json(client):
    resp = client.post(
        "/align",
        files={"file": ("doc.pdf", b"fake", "application/pdf")},
        data={"extracted": "not-json", "field_types": "{}"},
    )
    assert resp.status_code == 400


def test_align_alignment_failure_returns_422(client, monkeypatch):
    def fake_align_fields(extracted, field_types, doc_path):
        raise ValueError("boom")

    import services.bbox_generator.api as mod
    monkeypatch.setattr(mod, "align_fields", fake_align_fields)

    resp = client.post(
        "/align",
        files={"file": ("doc.pdf", b"fake", "application/pdf")},
        data={"extracted": "{}", "field_types": "{}"},
    )
    assert resp.status_code == 422
