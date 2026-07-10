"""
Tests for the bbox-generator FastAPI router. align_fields/align_extraction_llm
are unit tested in test_bbox_aligner.py/test_llm_bbox.py; here we only verify
the HTTP layer (request parsing, method dispatch, error handling, response
passthrough) by monkeypatching both.
"""
import json

import pytest
from fastapi.testclient import TestClient

from services.bbox_generator.api import app
from services.bbox_generator.llm_bbox import LlmConfigError


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


def test_align_defaults_to_ocr_method(client, monkeypatch):
    calls = {}

    def fake_align_fields(extracted, field_types, doc_path):
        calls["called"] = True
        return {}

    import services.bbox_generator.api as mod
    monkeypatch.setattr(mod, "align_fields", fake_align_fields)

    resp = client.post(
        "/align",
        files={"file": ("doc.pdf", b"fake", "application/pdf")},
        data={"extracted": "{}", "field_types": "{}"},
    )
    assert resp.status_code == 200
    assert calls.get("called") is True


def test_align_unknown_method_returns_400(client):
    resp = client.post(
        "/align",
        files={"file": ("doc.pdf", b"fake", "application/pdf")},
        data={"extracted": "{}", "field_types": "{}", "method": "magic"},
    )
    assert resp.status_code == 400


def test_align_llm_without_template_returns_400(client):
    resp = client.post(
        "/align",
        files={"file": ("doc.pdf", b"fake", "application/pdf")},
        data={"extracted": "{}", "field_types": "{}", "method": "llm"},
    )
    assert resp.status_code == 400


def test_align_llm_dispatches_to_align_extraction_llm(client, monkeypatch):
    def fake_align_extraction_llm(extracted, field_types, template, doc_path):
        return {k: {"value": v, "bbox": {"page": 1, "x0": 0, "y0": 0, "x1": 1, "y1": 1},
                    "match_quality": "llm_verified"} for k, v in extracted.items()}

    import services.bbox_generator.api as mod
    monkeypatch.setattr(mod, "align_extraction_llm", fake_align_extraction_llm)

    resp = client.post(
        "/align",
        files={"file": ("doc.pdf", b"fake", "application/pdf")},
        data={
            "extracted": json.dumps({"business_name": "Acme"}),
            "field_types": json.dumps({"business_name": "string"}),
            "method": "llm",
            "template": json.dumps({"key": "t", "fields": {}}),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["business_name"]["match_quality"] == "llm_verified"


def test_align_llm_config_error_returns_503(client, monkeypatch):
    def fake_align_extraction_llm(extracted, field_types, template, doc_path):
        raise LlmConfigError("GCP_PROJECT_ID is not set")

    import services.bbox_generator.api as mod
    monkeypatch.setattr(mod, "align_extraction_llm", fake_align_extraction_llm)

    resp = client.post(
        "/align",
        files={"file": ("doc.pdf", b"fake", "application/pdf")},
        data={
            "extracted": "{}",
            "field_types": "{}",
            "method": "llm",
            "template": json.dumps({"key": "t", "fields": {}}),
        },
    )
    assert resp.status_code == 503
