"""
Tests for app.config and app.schema_builder, run against the REAL bmmb_dev
Cloud SQL database (the 15 BMMB document templates, seeded from
seed_templates_attributes.sql) so a broken config is caught immediately, not
just a synthetic fixture. Requires Cloud SQL credentials -- see the `test`
job in .github/workflows/deploy.yml for how CI provides them.
"""
import pytest

from app.config import TemplateNotFoundError, get_template, list_templates
from app.schema_builder import build_gemini_schema, generate_extraction_prompt

COMPANY_ACT_SECTION_14 = 1  # 3 unique alphanumeric fields, no row_group
BANK_STATEMENTS = 9  # 4 "Multiple" fields (one numeric) + 1 "Unique"
FINANCIAL_STATEMENTS = 7  # includes a "Boolean" field
CUSTOMER_INFORMATION_FORM = 12  # 24 fields, mix of Unique/Multiple


class TestConfigLoading:
    def test_all_templates_present(self):
        ids = {t["id"] for t in list_templates()}
        assert ids == set(range(1, 16))

    def test_unknown_template_raises(self):
        with pytest.raises(TemplateNotFoundError):
            get_template(9999)

    @pytest.mark.parametrize("template_id", range(1, 16))
    def test_template_has_attributes(self, template_id):
        tmpl = get_template(template_id)
        assert tmpl["name"]
        assert len(tmpl["template_attributes"]) > 0

    def test_attribute_shape(self):
        tmpl = get_template(COMPANY_ACT_SECTION_14)
        ta = tmpl["template_attributes"][0]
        assert set(ta.keys()) == {"id", "attribute_id", "frequency", "row_group", "attribute"}
        assert ta["frequency"] in {"Unique", "Multiple"}
        attr = ta["attribute"]
        assert set(attr.keys()) == {"id", "name", "description", "data_type", "example"}
        assert attr["data_type"] in {"Alphabet", "Alphanumeric", "Numeric", "Datetime", "Boolean"}

    def test_llm_prompt_is_precomputed(self):
        # Seeded templates already carry a stored llm_prompt.
        tmpl = get_template(COMPANY_ACT_SECTION_14)
        assert tmpl["llm_prompt"]
        assert tmpl["name"] in tmpl["llm_prompt"]


class TestSchemaShape:
    def test_object_schema_for_ungrouped_template(self):
        schema = build_gemini_schema(COMPANY_ACT_SECTION_14)
        assert schema["type"] == "OBJECT"
        assert "properties" in schema

    def test_unknown_template_raises(self):
        with pytest.raises(TemplateNotFoundError):
            build_gemini_schema(9999)

    def test_multiple_frequency_produces_array(self):
        schema = build_gemini_schema(BANK_STATEMENTS)
        props = schema["properties"]
        assert props["Bank Statement Month"]["type"] == "ARRAY"
        assert props["Bank Statement Month"]["items"]["type"] == "STRING"

    def test_unique_frequency_produces_scalar(self):
        schema = build_gemini_schema(BANK_STATEMENTS)
        assert schema["properties"]["Document Type"]["type"] == "STRING"


class TestFieldTypeMapping:
    def test_numeric_multiple_maps_to_array_of_number(self):
        schema = build_gemini_schema(BANK_STATEMENTS)
        field = schema["properties"]["Monthly Withdrawal"]
        assert field["type"] == "ARRAY"
        assert field["items"]["type"] == "NUMBER"

    def test_boolean_maps_to_BOOLEAN(self):
        schema = build_gemini_schema(FINANCIAL_STATEMENTS)
        assert schema["properties"]["Balance Sheet Present"]["type"] == "BOOLEAN"

    def test_alphanumeric_maps_to_STRING(self):
        schema = build_gemini_schema(COMPANY_ACT_SECTION_14)
        assert schema["properties"]["MISC Code"]["type"] == "STRING"

    def test_all_fields_nullable(self):
        schema = build_gemini_schema(CUSTOMER_INFORMATION_FORM)
        for name, meta in schema["properties"].items():
            assert meta.get("nullable") is True, f"{name} is not nullable"

    def test_locations_present_for_every_field(self):
        schema = build_gemini_schema(CUSTOMER_INFORMATION_FORM)
        assert "_locations" in schema["properties"]
        location_fields = set(schema["properties"]["_locations"]["properties"])
        data_fields = set(schema["properties"]) - {"_locations"}
        assert location_fields == data_fields

    def test_location_schema_has_all_four_keys(self):
        schema = build_gemini_schema(COMPANY_ACT_SECTION_14)
        loc = schema["properties"]["_locations"]["properties"]["MISC Code"]
        assert set(loc["properties"]) == {"real_page", "shown_page", "section", "document"}


class TestPrompt:
    def test_prompt_is_non_empty(self):
        prompt = generate_extraction_prompt(COMPANY_ACT_SECTION_14)
        assert isinstance(prompt, str) and len(prompt) > 0

    def test_prompt_mentions_null_instruction(self):
        prompt = generate_extraction_prompt(COMPANY_ACT_SECTION_14)
        assert "null" in prompt.lower()

    def test_prompt_lists_every_field(self):
        tmpl = get_template(COMPANY_ACT_SECTION_14)
        prompt = generate_extraction_prompt(COMPANY_ACT_SECTION_14)
        for ta in tmpl["template_attributes"]:
            assert ta["attribute"]["name"] in prompt

    def test_unknown_template_raises(self):
        with pytest.raises(TemplateNotFoundError):
            generate_extraction_prompt(9999)
