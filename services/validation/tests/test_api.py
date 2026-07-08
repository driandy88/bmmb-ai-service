"""
End-to-end tests of the FastAPI wiring (routing, request/response shape,
error handling) for services/validation/api.py.

The deterministic-only path (enable_ai_review=False) needs no mocking at
all. The full agentic path monkeypatches genai.Client so these never call
the real Gemini/Vertex AI API — they run in CI with no credentials and no
network access, same convention as services/extraction/tests/test_api.py.
"""

from fastapi.testclient import TestClient

from services.validation.api import app
from services.validation.schemas import AIFinding, AIReview

client = TestClient(app)


class FakeGenaiClient:
    """Stands in for google.genai.Client(...).models.generate_content(...)."""

    def __init__(self, response_json: str):
        self._response_json = response_json
        self.models = self

    def generate_content(self, **kwargs):
        class _Response:
            pass

        r = _Response()
        r.text = self._response_json
        return r


class TestHealth:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestValidateDeterministicOnly:
    """enable_ai_review=False needs no GCP credentials — the common case for CI."""

    def test_passing_bundle(self, passing_bundle_raw):
        r = client.post(
            "/validate",
            json={"bundle": passing_bundle_raw, "enable_ai_review": False},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["deterministic"]["entity_name"] == "ALPHA TECH SOLUTIONS SDN BHD"
        assert all(res["passed"] is not False for res in body["deterministic"]["results"])
        assert body["ai_findings"] == []

    def test_failing_bundle_reports_deterministic_failure(self, failing_bundle_raw):
        r = client.post(
            "/validate",
            json={"bundle": failing_bundle_raw, "enable_ai_review": False},
        )
        assert r.status_code == 200
        body = r.json()
        assert any(res["passed"] is False for res in body["deterministic"]["results"])

    def test_malformed_bundle_returns_422(self):
        r = client.post(
            "/validate",
            json={"bundle": {"bundle_id": "B1"}, "enable_ai_review": False},
        )
        assert r.status_code == 422


class TestValidateAgenticPath:
    def test_missing_gcp_project_id_returns_503(self, passing_bundle_raw, monkeypatch):
        monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
        r = client.post("/validate", json={"bundle": passing_bundle_raw})
        assert r.status_code == 503
        assert "GCP_PROJECT_ID" in r.json()["detail"]

    def test_ai_findings_are_included_when_review_succeeds(self, passing_bundle_raw, monkeypatch):
        monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
        fake_review = AIReview(
            ai_findings=[AIFinding(finding="Test finding", severity="warning", detail="Test detail")],
            narrative="Looks fine overall.",
        )
        monkeypatch.setattr(
            "services.validation.agent.genai.Client",
            lambda **kwargs: FakeGenaiClient(fake_review.model_dump_json()),
        )

        r = client.post("/validate", json={"bundle": passing_bundle_raw})
        assert r.status_code == 200
        body = r.json()
        assert body["ai_findings"] == [
            {"finding": "Test finding", "severity": "warning", "detail": "Test detail"}
        ]
        assert body["narrative"] == "Looks fine overall."
        # Deterministic verdict always comes from ValidationEngine, never the model.
        assert body["deterministic"]["entity_name"] == "ALPHA TECH SOLUTIONS SDN BHD"

    def test_severity_outside_allowed_values_is_clamped(self, passing_bundle_raw, monkeypatch):
        monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
        fake_review = AIReview(
            ai_findings=[AIFinding(finding="Suspicious", severity="fail", detail="Should never be a verdict.")],
            narrative="n/a",
        )
        monkeypatch.setattr(
            "services.validation.agent.genai.Client",
            lambda **kwargs: FakeGenaiClient(fake_review.model_dump_json()),
        )

        r = client.post("/validate", json={"bundle": passing_bundle_raw})
        assert r.status_code == 200
        assert r.json()["ai_findings"][0]["severity"] == "needs_review"

    def test_malformed_model_response_falls_back_to_deterministic_only(self, passing_bundle_raw, monkeypatch):
        monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
        monkeypatch.setattr(
            "services.validation.agent.genai.Client",
            lambda **kwargs: FakeGenaiClient("not valid json"),
        )

        r = client.post("/validate", json={"bundle": passing_bundle_raw})
        assert r.status_code == 200
        body = r.json()
        assert body["ai_findings"] == []
        assert "failed to parse" in body["narrative"].lower()


class TestConflictExampleEndToEnd:
    """The scenario adapter.py/examples/test_conflict_example.py document:
    deterministic engine alone sees a real-looking consent-form failure that
    the raw extraction shows is actually an adapter mapping artifact."""

    def test_adapter_bug_produces_a_deterministic_failure(self, raw_extraction_conflict):
        from services.validation.adapter import adapt_raw_extraction

        bundle = adapt_raw_extraction(raw_extraction_conflict)
        r = client.post(
            "/validate",
            json={"bundle": bundle.model_dump(mode="json"), "enable_ai_review": False},
        )
        assert r.status_code == 200
        body = r.json()
        consent_check = next(
            res for res in body["deterministic"]["results"] if res["check"] == "verify_consent_signatures"
        )
        assert consent_check["passed"] is False
