"""
End-to-end tests of the FastAPI wiring (routing, validation, error handling)
with app.extraction.run_extraction monkeypatched -- these never call the real
Gemini/Vertex AI API, so they run in CI with no credentials and no network access.
"""
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

TINY_PDF = b"%PDF-1.4\n%mock pdf bytes for testing only\n%%EOF"
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "3df40000000a4944415478da6360000002000155a2d0870000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def mock_gemini(monkeypatch):
    """Replaces the real Gemini/Vertex AI call with a canned response for every test."""
    def _fake_run_extraction(file_bytes, mime_type, prompt, schema, model="gemini-2.5-flash"):
        if schema.get("type") == "ARRAY":
            return [{"mocked": True}]
        return {"mocked": True}

    monkeypatch.setattr("app.extraction.run_extraction", _fake_run_extraction)


class TestHealthAndTemplates:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_list_templates(self):
        r = client.get("/templates")
        assert r.status_code == 200
        keys = {t["key"] for t in r.json()}
        assert "business_registration_ssm" in keys

    def test_get_template_detail(self):
        r = client.get("/templates/consent_form")
        assert r.status_code == 200
        assert "authorized_names" in r.json()["fields"]

    def test_get_unknown_template_404(self):
        r = client.get("/templates/does_not_exist")
        assert r.status_code == 404


class TestExtractValidation:
    def test_unknown_template_404(self):
        r = client.post(
            "/extract",
            data={"template": "nope"},
            files={"file": ("doc.pdf", io.BytesIO(TINY_PDF), "application/pdf")},
        )
        assert r.status_code == 404

    def test_unsupported_mime_type_400(self):
        r = client.post(
            "/extract",
            data={"template": "consent_form"},
            files={"file": ("doc.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert r.status_code == 400

    def test_empty_file_400(self):
        r = client.post(
            "/extract",
            data={"template": "consent_form"},
            files={"file": ("doc.pdf", io.BytesIO(b""), "application/pdf")},
        )
        assert r.status_code == 400

    def test_missing_template_422(self):
        r = client.post(
            "/extract",
            data={},
            files={"file": ("doc.pdf", io.BytesIO(TINY_PDF), "application/pdf")},
        )
        assert r.status_code == 422  # FastAPI form validation


class TestExtractSuccess:
    def test_pdf_extraction(self):
        r = client.post(
            "/extract",
            data={"template": "business_registration_ssm"},
            files={"file": ("ssm.pdf", io.BytesIO(TINY_PDF), "application/pdf")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["data"]["template"] == "business_registration_ssm"
        assert body["data"]["template_kind"] == "single"
        assert body["data"]["extracted_data"] == {"mocked": True}

    def test_image_extraction(self):
        r = client.post(
            "/extract",
            data={"template": "ic_photocopies"},
            files={"file": ("ic.png", io.BytesIO(TINY_PNG), "image/png")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["template_kind"] == "array"
        assert body["data"]["extracted_data"] == [{"mocked": True}]

    def test_custom_model_passed_through(self):
        r = client.post(
            "/extract",
            data={"template": "consent_form", "model": "gemini-2.0-flash"},
            files={"file": ("doc.pdf", io.BytesIO(TINY_PDF), "application/pdf")},
        )
        assert r.status_code == 200
        assert r.json()["data"]["model"] == "gemini-2.0-flash"


class TestMissingConfig:
    def test_missing_gcp_project_id_returns_500(self, monkeypatch):
        """If GCP_PROJECT_ID isn't set, the real run_extraction raises
        GeminiConfigError -- confirm the endpoint surfaces that as a 500,
        not an unhandled crash. Bypasses the autouse mock for this one test."""
        import app.gemini_client as gc
        from app.gemini_client import GeminiConfigError

        def _raise_config_error(*args, **kwargs):
            raise GeminiConfigError("GCP_PROJECT_ID is not set.")

        monkeypatch.setattr("app.extraction.run_extraction", _raise_config_error)

        r = client.post(
            "/extract",
            data={"template": "consent_form"},
            files={"file": ("doc.pdf", io.BytesIO(TINY_PDF), "application/pdf")},
        )
        assert r.status_code == 500
        assert "misconfigured" in r.json()["detail"].lower()