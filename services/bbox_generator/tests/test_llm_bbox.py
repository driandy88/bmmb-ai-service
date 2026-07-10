"""
Tests for llm_bbox.py. All Gemini calls are monkeypatched via FakeGenaiClient
(same convention as services/validation/tests/test_api.py) — these never
call the real Gemini/Vertex AI API, so they run in CI with no credentials.
"""
import json

import pytest

from services.bbox_generator.llm_bbox import (
    LlmConfigError,
    align_extraction_llm,
    build_prompt,
    evaluate,
    get_client,
    iou,
    verify_llm_box,
)


def _word(text, page=1, x0=0.0, y0=0.0, x1=0.1, y1=0.05):
    return {"page": page, "text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


class FakeGenaiClient:
    """Stands in for google.genai.Client(...).models.generate_content(...)."""

    def __init__(self, responses):
        # responses: list of JSON strings, one per expected call, consumed in order
        self._responses = list(responses)
        self.calls = []
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)

        class _Response:
            pass

        r = _Response()
        r.text = self._responses.pop(0)
        return r


class TestBuildPrompt:
    def test_includes_template_and_field_metadata(self):
        template = {
            "key": "business_registration_ssm",
            "name": "Business Registration",
            "description": "SSM certificate",
            "fields": {"business_name": {"description": "Registered company name"}},
        }
        prompt = build_prompt(template, {"business_name": "Acme Sdn Bhd"})
        assert "Business Registration" in prompt
        assert "SSM certificate" in prompt
        assert "business_name" in prompt
        assert "Acme Sdn Bhd" in prompt
        assert "Registered company name" in prompt

    def test_missing_field_description_falls_back(self):
        template = {"key": "t", "fields": {}}
        prompt = build_prompt(template, {"some_field": "value"})
        assert "(no description)" in prompt

    def test_falls_back_to_key_when_no_name(self):
        template = {"key": "bank_statements", "fields": {}}
        prompt = build_prompt(template, {})
        assert "bank_statements" in prompt


class TestVerifyLlmBox:
    def test_no_box(self):
        assert verify_llm_box(None, "value", []) == "no_box"

    def test_verified_string(self):
        bbox = {"page": 1, "x0": 0.0, "y0": 0.0, "x1": 0.3, "y1": 0.05}
        words = [_word("SEBASTIAN", x0=0.05, x1=0.25)]
        assert verify_llm_box(bbox, "SEBASTIAN", words) == "verified"

    def test_unverified_when_nothing_inside_box(self):
        bbox = {"page": 1, "x0": 0.6, "y0": 0.6, "x1": 0.9, "y1": 0.65}
        words = [_word("SEBASTIAN", x0=0.05, x1=0.25)]
        assert verify_llm_box(bbox, "SEBASTIAN", words) == "unverified"

    def test_unverified_when_box_contains_wrong_text(self):
        bbox = {"page": 1, "x0": 0.0, "y0": 0.0, "x1": 0.3, "y1": 0.05}
        words = [_word("WRONGVALUE", x0=0.05, x1=0.25)]
        assert verify_llm_box(bbox, "SEBASTIAN", words) == "unverified"

    def test_verified_number_with_different_formatting(self):
        bbox = {"page": 1, "x0": 0.0, "y0": 0.0, "x1": 0.3, "y1": 0.05}
        words = [_word("RM", x0=0.0, x1=0.05), _word("328,400.00", x0=0.05, x1=0.25)]
        assert verify_llm_box(bbox, 328400.0, words) == "verified"

    def test_verified_date_month_granularity(self):
        bbox = {"page": 1, "x0": 0.0, "y0": 0.0, "x1": 0.3, "y1": 0.05}
        words = [_word("Julai", x0=0.0, x1=0.1), _word("2023", x0=0.1, x1=0.2)]
        assert verify_llm_box(bbox, "July 2023", words) == "verified"

    def test_page_mismatch_finds_nothing_inside(self):
        bbox = {"page": 2, "x0": 0.0, "y0": 0.0, "x1": 0.3, "y1": 0.05}
        words = [_word("SEBASTIAN", page=1, x0=0.05, x1=0.25)]
        assert verify_llm_box(bbox, "SEBASTIAN", words) == "unverified"


class TestIouAndEvaluate:
    def test_identical_boxes_iou_1(self):
        b = {"page": 1, "x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.3}
        assert iou(b, dict(b)) == 1.0

    def test_disjoint_boxes_iou_0(self):
        a = {"page": 1, "x0": 0.0, "y0": 0.0, "x1": 0.1, "y1": 0.1}
        b = {"page": 1, "x0": 0.5, "y0": 0.5, "x1": 0.6, "y1": 0.6}
        assert iou(a, b) == 0.0

    def test_different_pages_iou_0(self):
        a = {"page": 1, "x0": 0.0, "y0": 0.0, "x1": 0.5, "y1": 0.5}
        b = {"page": 2, "x0": 0.0, "y0": 0.0, "x1": 0.5, "y1": 0.5}
        assert iou(a, b) == 0.0

    def test_evaluate_reports_iou_per_field(self):
        llm = {"f": {"bbox": {"page": 1, "x0": 0, "y0": 0, "x1": 1, "y1": 1}}}
        aligner = {"f": {"bbox": {"page": 1, "x0": 0, "y0": 0, "x1": 1, "y1": 1}}}
        report = evaluate(llm, aligner)
        assert report["f"]["iou"] == 1.0
        assert report["f"]["llm_found"] and report["f"]["aligner_found"]


class TestGetClient:
    def test_raises_without_project_id(self, monkeypatch):
        monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
        import services.bbox_generator.llm_bbox as mod
        monkeypatch.setattr(mod, "GCP_PROJECT_ID", None)
        with pytest.raises(LlmConfigError):
            get_client()


class TestAlignExtractionLlm:
    def _patch_common(self, monkeypatch, words, page_images=None):
        import services.bbox_generator.llm_bbox as mod
        monkeypatch.setattr(mod, "_load_words", lambda path: words)
        monkeypatch.setattr(mod, "render_pages", lambda path: page_images or [b"page1png"])

    def test_single_record_verified_on_first_page(self, monkeypatch):
        words = [_word("Acme", x0=0.0, x1=0.2)]
        self._patch_common(monkeypatch, words)
        client = FakeGenaiClient([
            json.dumps([{"field": "business_name", "found": True, "box_2d": [0, 0, 50, 200]}]),
        ])
        template = {"key": "t", "fields": {"business_name": {"description": "Company name"}}}
        result = align_extraction_llm(
            {"business_name": "Acme"}, {"business_name": "string"}, template, "doc.pdf", client=client
        )
        assert result["business_name"]["match_quality"] == "llm_verified"
        assert result["business_name"]["bbox"]["page"] == 1

    def test_not_found_flag_skips_verification(self, monkeypatch):
        words = [_word("Acme", x0=0.0, x1=0.2)]
        self._patch_common(monkeypatch, words)
        client = FakeGenaiClient([
            json.dumps([{"field": "business_name", "found": False}]),
        ])
        template = {"key": "t", "fields": {}}
        result = align_extraction_llm(
            {"business_name": "Acme"}, {"business_name": "string"}, template, "doc.pdf", client=client
        )
        assert result["business_name"]["bbox"] is None
        assert result["business_name"]["match_quality"] == "not_found"

    def test_unverified_box_kept_as_fallback_when_no_better_page(self, monkeypatch):
        # LLM finds a box but the words inside don't actually match the value
        words = [_word("WrongText", x0=0.0, x1=0.2)]
        self._patch_common(monkeypatch, words)
        client = FakeGenaiClient([
            json.dumps([{"field": "business_name", "found": True, "box_2d": [0, 0, 50, 200]}]),
        ])
        template = {"key": "t", "fields": {}}
        result = align_extraction_llm(
            {"business_name": "Acme"}, {"business_name": "string"}, template, "doc.pdf", client=client
        )
        assert result["business_name"]["match_quality"] == "llm_unverified"
        assert result["business_name"]["bbox"] is not None

    def test_null_value_short_circuits_without_calling_gemini(self, monkeypatch):
        self._patch_common(monkeypatch, [])
        client = FakeGenaiClient([])  # no responses queued -- would error if called
        template = {"key": "t", "fields": {}}
        result = align_extraction_llm(
            {"business_name": None}, {"business_name": "string"}, template, "doc.pdf", client=client
        )
        assert result == {"business_name": {"value": None, "bbox": None, "match_quality": "null_value"}}
        assert client.calls == []

    def test_array_kind_returns_list_one_result_per_record(self, monkeypatch):
        words = [_word("January", x0=0.0, x1=0.2), _word("February", x0=0.0, x1=0.2, y0=0.2, y1=0.25)]
        self._patch_common(monkeypatch, words)
        client = FakeGenaiClient([
            json.dumps([{"field": "month", "found": True, "box_2d": [0, 0, 50, 200]}]),
            json.dumps([{"field": "month", "found": True, "box_2d": [200, 0, 250, 200]}]),
        ])
        template = {"key": "t", "fields": {}}
        records = [{"month": "January"}, {"month": "February"}]
        result = align_extraction_llm(records, {"month": "string"}, template, "doc.pdf", client=client)
        assert isinstance(result, list) and len(result) == 2
        assert result[0]["month"]["match_quality"] == "llm_verified"
        assert result[1]["month"]["match_quality"] == "llm_verified"

    def test_underscore_prefixed_keys_excluded(self, monkeypatch):
        words = [_word("Acme", x0=0.0, x1=0.2)]
        self._patch_common(monkeypatch, words)
        client = FakeGenaiClient([
            json.dumps([{"field": "business_name", "found": True, "box_2d": [0, 0, 50, 200]}]),
        ])
        template = {"key": "t", "fields": {}}
        record = {"_locations": {}, "business_name": "Acme"}
        result = align_extraction_llm(record, {"business_name": "string"}, template, "doc.pdf", client=client)
        assert "_locations" not in result
        assert result["business_name"]["match_quality"] == "llm_verified"
