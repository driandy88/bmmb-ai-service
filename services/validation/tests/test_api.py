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


def _all_checks(body: dict) -> list:
    """Flatten the grouped deterministic checks.

    The flat `deterministic.results` list is excluded from the HTTP response
    (see api.py); the grouped `deterministic.results_by_document` is the shape
    the frontend consumes, so tests read checks through it too.
    """
    return [
        check
        for checks in body["deterministic"]["results_by_document"].values()
        for check in checks
    ]


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

    def test_rules_catalog(self):
        r = client.get("/rules")
        assert r.status_code == 200
        body = r.json()
        assert body["policy_id"] == "bmmb-sme-2026-01"
        assert any(rule["rule_id"] == "bank_statement.duration" for rule in body["rules"])


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
        assert all(res["passed"] is not False for res in _all_checks(body))
        assert body["ai_findings"] == []

    def test_results_are_grouped_by_document_and_flat_list_is_omitted(self, passing_bundle_raw):
        r = client.post(
            "/validate",
            json={"bundle": passing_bundle_raw, "enable_ai_review": False},
        )
        assert r.status_code == 200
        body = r.json()
        # Grouped view lives under `deterministic`; the flat per-rule `results`
        # list and the old top-level duplicate are both gone from the response.
        assert "results" not in body["deterministic"]
        assert "results_by_document" not in body
        grouped = body["deterministic"]["results_by_document"]
        assert set(grouped) == {
            "SSM_CORPORATE_FORM", "FINANCIAL_STATEMENT", "BANK_STATEMENT",
            "IDENTITY_DOCUMENT",
        }

    def test_failing_bundle_reports_deterministic_failure(self, failing_bundle_raw):
        r = client.post(
            "/validate",
            json={"bundle": failing_bundle_raw, "enable_ai_review": False},
        )
        assert r.status_code == 200
        body = r.json()
        assert any(res["passed"] is False for res in _all_checks(body))

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


class TestValidateFromExtraction:
    """POST /validate/from-extraction -- body is a bare extraction results
    dump (no wrapper), everything else is an optional query param."""

    def _extraction_results(self):
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[1]
            / "examples" / "extraction_results_example.json"
        )
        raw = json.loads(path.read_text())
        raw.pop("_comment", None)
        return raw

    def test_bare_extraction_dump_as_body_succeeds(self):
        r = client.post(
            "/validate/from-extraction",
            params={"enable_ai_review": False},
            json=self._extraction_results(),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["deterministic"]["entity_name"] == "ALPHA TECH SOLUTIONS SDN BHD"

    def test_no_field_ever_causes_a_400(self):
        # Nothing supplied beyond the bare dump -- tenure_months etc. are
        # all missing, but this must still succeed with warnings, not fail.
        r = client.post(
            "/validate/from-extraction",
            params={"enable_ai_review": False},
            json=self._extraction_results(),
        )
        assert r.status_code == 200
        assert len(r.json()["adapter_warnings"]) > 0

    def test_query_param_overrides_are_applied(self):
        r = client.post(
            "/validate/from-extraction",
            params={
                "enable_ai_review": False,
                "tenure_months": 24,
                "repayment_frequency": "Quarterly",
                "signature_present": True,
            },
            json=self._extraction_results(),
        )
        assert r.status_code == 200
        fields = {w["field"] for w in r.json()["adapter_warnings"]}
        assert "tenure_months" not in fields
        assert "repayment_frequency" not in fields
        assert "signature_present" not in fields
