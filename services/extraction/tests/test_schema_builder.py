"""
Tests for app.config and app.schema_builder, run against the REAL
templates_config.json (the 6 SME-financing document templates) so a broken
config is caught immediately, not just a synthetic fixture.
"""
import pytest

from app.config import TemplateNotFoundError, get_template, list_templates
from app.schema_builder import build_gemini_schema, generate_extraction_prompt

REAL_TEMPLATE_KEYS = {
    "business_registration_ssm",
    "audited_financial_statements",
    "bank_statements",
    "ic_photocopies",
    "consent_form",
    "customer_information_details",
}


class TestConfigLoading:
    def test_all_expected_templates_present(self):
        keys = {t["key"] for t in list_templates()}
        assert REAL_TEMPLATE_KEYS <= keys

    def test_unknown_template_raises(self):
        with pytest.raises(TemplateNotFoundError):
            get_template("does_not_exist")

    @pytest.mark.parametrize("key", sorted(REAL_TEMPLATE_KEYS))
    def test_template_has_fields(self, key):
        tmpl = get_template(key)
        assert tmpl["kind"] in {"single", "array"}
        assert len(tmpl["fields"]) > 0


class TestSchemaKind:
    def test_single_kind_templates_produce_object_schema(self):
        # business_registration_ssm uses "fields" -> single object per document
        schema = build_gemini_schema("business_registration_ssm")
        assert schema["type"] == "OBJECT"
        assert "properties" in schema

    def test_array_kind_templates_produce_array_schema(self):
        # bank_statements uses "statement_object_fields" -> array of objects
        schema = build_gemini_schema("bank_statements")
        assert schema["type"] == "ARRAY"
        assert schema["items"]["type"] == "OBJECT"

    def test_unknown_template_raises(self):
        with pytest.raises(TemplateNotFoundError):
            build_gemini_schema("nope")


class TestFieldTypeMapping:
    def test_string_field_maps_to_STRING(self):
        schema = build_gemini_schema("business_registration_ssm")
        assert schema["properties"]["business_name"]["type"] == "STRING"

    def test_float_field_maps_to_NUMBER(self):
        schema = build_gemini_schema("bank_statements")
        props = schema["items"]["properties"]
        assert props["monthly_withdrawal"]["type"] == "NUMBER"

    def test_date_field_maps_to_STRING(self):
        schema = build_gemini_schema("business_registration_ssm")
        assert schema["properties"]["incorporation_date"]["type"] == "STRING"

    def test_list_string_field_maps_to_array_of_string(self):
        schema = build_gemini_schema("consent_form")
        field = schema["properties"]["authorized_names"]
        assert field["type"] == "ARRAY"
        assert field["items"]["type"] == "STRING"

    def test_all_fields_nullable(self):
        schema = build_gemini_schema("customer_information_details")
        for name, meta in schema["properties"].items():
            assert meta.get("nullable") is True, f"{name} is not nullable"

    def test_all_fields_in_required(self):
        # required + nullable is the mechanism that forces Gemini to return
        # null instead of omitting a key it couldn't find.
        schema = build_gemini_schema("customer_information_details")
        assert set(schema["required"]) == set(schema["properties"].keys())


class TestPrompt:
    def test_prompt_is_non_empty(self):
        prompt = generate_extraction_prompt("business_registration_ssm")
        assert isinstance(prompt, str) and len(prompt) > 0

    def test_prompt_mentions_null_instruction(self):
        prompt = generate_extraction_prompt("business_registration_ssm")
        assert "null" in prompt.lower()

    def test_prompt_lists_every_field(self):
        tmpl = get_template("customer_information_details")
        prompt = generate_extraction_prompt("customer_information_details")
        for field_name in tmpl["fields"]:
            assert field_name in prompt

    def test_array_kind_prompt_mentions_multiple_instances(self):
        prompt = generate_extraction_prompt("bank_statements")
        assert "array" in prompt.lower() or "multiple" in prompt.lower()

    def test_unknown_template_raises(self):
        with pytest.raises(TemplateNotFoundError):
            generate_extraction_prompt("nope")
