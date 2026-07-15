"""
End-to-end tests of the FastAPI wiring (routing, validation, error handling)
with app.extraction.run_extraction monkeypatched -- these never call the real
Gemini/Vertex AI API, so they run in CI with no credentials and no network access.

/templates and /templates/{id} still hit the real bmmb_dev Cloud SQL database
(same as test_schema_builder.py) since app.config is not mocked here.
"""
import io

import pytest
from fastapi.testclient import TestClient

from app.config import list_templates
from app.main import app

client = TestClient(app)

TINY_PDF = b"%PDF-1.4\n%mock pdf bytes for testing only\n%%EOF"
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "3df40000000a4944415478da6360000002000155a2d0870000000049454e44ae426082"
)

# Ids are DB-generated uuids (not stable across re-seeds), so look them up by
# name -- the templates themselves are stable, seeded via
# seed_templates_attributes.sql.
_BY_NAME = {t["name"]: t["id"] for t in list_templates()}
COMPANY_ACT_SECTION_14 = _BY_NAME["Company Act Section 14"]  # single-object schema, no "Multiple" fields
BANK_STATEMENTS = _BY_NAME["Bank Statements"]  # has "Multiple" fields -> array-valued properties
UNKNOWN_TEMPLATE_ID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def mock_gemini(monkeypatch):
    """Replaces the real Gemini/Vertex AI call with a canned response for every test."""
    def _fake_run_extraction(files, prompt, schema, model="gemini-2.5-flash"):
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
        ids = {t["id"] for t in r.json()}
        assert COMPANY_ACT_SECTION_14 in ids

    def test_get_template_detail(self):
        r = client.get(f"/templates/{BANK_STATEMENTS}")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Bank Statements"
        names = {ta["attribute"]["name"] for ta in body["template_attributes"]}
        assert "Bank Name" in names

    def test_get_unknown_template_404(self):
        r = client.get(f"/templates/{UNKNOWN_TEMPLATE_ID}")
        assert r.status_code == 404

    def test_get_template_malformed_id_404(self):
        # template_id is a plain str path param now (uuid, not int) -- any
        # non-matching string 404s as "not found" rather than 422 validation.
        r = client.get("/templates/not-a-uuid")
        assert r.status_code == 404


class TestExtractValidation:
    def test_unknown_template_404(self):
        r = client.post(
            "/extract",
            data={"template_id": UNKNOWN_TEMPLATE_ID},
            files={"files": ("doc.pdf", io.BytesIO(TINY_PDF), "application/pdf")},
        )
        assert r.status_code == 404

    def test_unsupported_mime_type_400(self):
        r = client.post(
            "/extract",
            data={"template_id": COMPANY_ACT_SECTION_14},
            files={"files": ("doc.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert r.status_code == 400

    def test_empty_file_400(self):
        r = client.post(
            "/extract",
            data={"template_id": COMPANY_ACT_SECTION_14},
            files={"files": ("doc.pdf", io.BytesIO(b""), "application/pdf")},
        )
        assert r.status_code == 400

    def test_missing_template_422(self):
        r = client.post(
            "/extract",
            data={},
            files={"files": ("doc.pdf", io.BytesIO(TINY_PDF), "application/pdf")},
        )
        assert r.status_code == 422  # FastAPI form validation


class TestExtractSuccess:
    def test_pdf_extraction(self):
        r = client.post(
            "/extract",
            data={"template_id": COMPANY_ACT_SECTION_14},
            files={"files": ("doc.pdf", io.BytesIO(TINY_PDF), "application/pdf")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["data"]["template_id"] == COMPANY_ACT_SECTION_14
        assert body["data"]["template_name"] == "Company Act Section 14"
        assert body["data"]["documents"] == ["doc.pdf"]
        assert body["data"]["extracted_data"] == {"mocked": True}

    def test_multi_file_extraction(self):
        r = client.post(
            "/extract",
            data={"template_id": BANK_STATEMENTS},
            files=[
                ("files", ("jan.pdf", io.BytesIO(TINY_PDF), "application/pdf")),
                ("files", ("feb.pdf", io.BytesIO(TINY_PDF), "application/pdf")),
            ],
        )
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["documents"] == ["jan.pdf", "feb.pdf"]

    def test_image_extraction(self):
        r = client.post(
            "/extract",
            data={"template_id": COMPANY_ACT_SECTION_14},
            files={"files": ("doc.png", io.BytesIO(TINY_PNG), "image/png")},
        )
        assert r.status_code == 200

    def test_custom_model_passed_through(self):
        r = client.post(
            "/extract",
            data={"template_id": COMPANY_ACT_SECTION_14, "model": "gemini-2.0-flash"},
            files={"files": ("doc.pdf", io.BytesIO(TINY_PDF), "application/pdf")},
        )
        assert r.status_code == 200
        assert r.json()["data"]["model"] == "gemini-2.0-flash"


class TestMissingConfig:
    def test_missing_gcp_project_id_returns_500(self, monkeypatch):
        """If GCP_PROJECT_ID isn't set, the real run_extraction raises
        GeminiConfigError -- confirm the endpoint surfaces that as a 500,
        not an unhandled crash. Bypasses the autouse mock for this one test."""
        from app.gemini_client import GeminiConfigError

        def _raise_config_error(*args, **kwargs):
            raise GeminiConfigError("GCP_PROJECT_ID is not set.")

        monkeypatch.setattr("app.extraction.run_extraction", _raise_config_error)

        r = client.post(
            "/extract",
            data={"template_id": COMPANY_ACT_SECTION_14},
            files={"files": ("doc.pdf", io.BytesIO(TINY_PDF), "application/pdf")},
        )
        assert r.status_code == 500
        assert "misconfigured" in r.json()["detail"].lower()
