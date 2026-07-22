"""
Tests for app.config and app.schema_builder, run against the REAL bmmb_dev
Cloud SQL database (the 15 seeded BMMB document templates, from
seed_templates_attributes.sql) so a broken config is caught immediately, not
just a synthetic fixture. Requires Cloud SQL credentials -- see the `test`
job in .github/workflows/deploy.yml for how CI provides them.

Assertions below check for the presence of these 15 known templates rather
than an exact row count/exact id list: test_crud.py's CRUD tests create and
delete throwaway "__test_tmpl__"/"__test_attr__" rows in this same real
database, and a run that fails partway through (before its own cleanup)
leaves one behind -- which must not fail *these* tests too.
"""
import pytest

from app.config import TemplateNotFoundError, get_template, list_templates
from app.schema_builder import build_gemini_schema, generate_extraction_prompt, reshape_locations

_SEEDED_TEMPLATE_NAMES = {
    "Company Act Section 14", "SSM Form 24", "SSM Form 44", "SSM Form 49",
    "SSM Form 9 & 28", "Form 32A", "Financial Statements (Sdn Bhd)", "Borang B",
    "Bank Statements", "MyKad (Director ID or Passport)", "Consent Form",
    "Customer Information Form", "Application Details", "CTOS Report",
    "CCRIS / CBM Report",
}

# Ids are DB-generated uuids (not stable across re-seeds), so look them up by
# name -- the templates themselves are stable, seeded via
# seed_templates_attributes.sql.
_ALL_TEMPLATES = list_templates()
_BY_NAME = {t["name"]: t["id"] for t in _ALL_TEMPLATES if t["name"] in _SEEDED_TEMPLATE_NAMES}

COMPANY_ACT_SECTION_14 = _BY_NAME["Company Act Section 14"]  # 3 unique alphanumeric fields, no row_group
BANK_STATEMENTS = _BY_NAME["Bank Statements"]  # 4 "Unique" header fields + a "Transactions" row_group
FINANCIAL_STATEMENTS = _BY_NAME["Financial Statements (Sdn Bhd)"]  # includes a "Boolean" field
CUSTOMER_INFORMATION_FORM = _BY_NAME["Customer Information Form"]  # 24 fields, mix of Unique/Multiple

UNKNOWN_TEMPLATE_ID = "00000000-0000-0000-0000-000000000000"


def _ta(name, data_type, frequency, row_group=None):
    """One synthetic template_attribute entry in get_template()'s normalised shape."""
    return {"frequency": frequency, "row_group": row_group,
            "attribute": {"name": name, "data_type": data_type, "description": "", "example": ""}}


def _schema_from_attrs(monkeypatch, attrs):
    """Build a Gemini schema from a synthetic in-memory template, so schema-shape
    behaviour checks don't depend on any live template's current wiring -- templates
    get migrated (bank/FS Multiple fields moved into row_groups), which would
    otherwise keep breaking these."""
    tmpl = {"id": "synthetic", "name": "Synthetic", "description": "",
            "group_name": None, "llm_prompt": None, "template_attributes": attrs}
    monkeypatch.setattr("app.schema_builder.get_template", lambda _id: tmpl)
    return build_gemini_schema("synthetic")


class TestConfigLoading:
    def test_all_templates_present(self):
        names = {t["name"] for t in list_templates()}
        assert _SEEDED_TEMPLATE_NAMES <= names

    def test_unknown_template_raises(self):
        with pytest.raises(TemplateNotFoundError):
            get_template(UNKNOWN_TEMPLATE_ID)

    @pytest.mark.parametrize("template_id", list(_BY_NAME.values()))
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
            build_gemini_schema(UNKNOWN_TEMPLATE_ID)

    def test_multiple_frequency_produces_array(self, monkeypatch):
        schema = _schema_from_attrs(monkeypatch, [_ta("Some Date", "Datetime", "Multiple")])
        field = schema["properties"]["Some Date"]
        assert field["type"] == "ARRAY"
        assert field["items"]["type"] == "STRING"

    def test_unique_frequency_produces_scalar(self):
        schema = build_gemini_schema(BANK_STATEMENTS)
        assert schema["properties"]["Document Type"]["type"] == "STRING"

    def test_row_group_produces_array_of_objects(self):
        # Bank Statements' daily transactions are a row_group: one array of
        # correlated row-objects, keyed by the group name.
        schema = build_gemini_schema(BANK_STATEMENTS)
        txns = schema["properties"]["Transactions"]
        assert txns["type"] == "ARRAY"
        assert txns["items"]["type"] == "OBJECT"
        cols = txns["items"]["properties"]
        assert "Transaction Date" in cols and "Transaction Balance" in cols


class TestRequiredKeys:
    def test_top_level_data_fields_required_locations_optional(self):
        # Every data field/group is required (so its key is always emitted),
        # but the advisory _locations metadata block is left optional.
        schema = build_gemini_schema(BANK_STATEMENTS)
        required = set(schema["required"])
        data_fields = set(schema["properties"]) - {"_locations"}
        assert required == data_fields
        assert "_locations" not in required

    def test_row_group_columns_all_required(self):
        schema = build_gemini_schema(BANK_STATEMENTS)
        txns = schema["properties"]["Transactions"]
        assert set(txns["items"]["required"]) == set(txns["items"]["properties"])

    def test_required_fields_are_still_nullable(self):
        # required + nullable together mean "the key must be present, value may
        # be null" -- so a missing value is an explicit null, never a dropped key.
        schema = build_gemini_schema(BANK_STATEMENTS)
        for name in schema["required"]:
            assert schema["properties"][name].get("nullable") is True, name


class TestFieldTypeMapping:
    def test_numeric_multiple_maps_to_array_of_number(self, monkeypatch):
        schema = _schema_from_attrs(monkeypatch, [_ta("Some Amount", "Numeric", "Multiple")])
        field = schema["properties"]["Some Amount"]
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


class TestRowGroupLocations:
    def test_row_group_location_is_a_per_row_list(self, monkeypatch):
        # A row_group's provenance is a LIST (one entry per row), not a single
        # location object like an ungrouped field -- each row can come from a
        # different file/page.
        schema = _schema_from_attrs(monkeypatch, [
            _ta("Financial Statement Date", "Datetime", "Multiple", row_group="Financials By Year"),
            _ta("Revenue", "Numeric", "Multiple", row_group="Financials By Year"),
        ])
        loc = schema["properties"]["_locations"]["properties"]["Financials By Year"]
        assert loc["type"] == "ARRAY"
        item = loc["items"]
        assert item["type"] == "OBJECT"
        # a `_row_key` to match the entry back to its data row, plus a full
        # location object per column
        assert set(item["properties"]) == {"_row_key", "Financial Statement Date", "Revenue"}
        assert item["properties"]["_row_key"]["type"] == "STRING"
        assert set(item["properties"]["Revenue"]["properties"]) == {
            "real_page", "shown_page", "section", "document"}

    def test_ungrouped_field_location_stays_a_single_object(self, monkeypatch):
        schema = _schema_from_attrs(monkeypatch, [_ta("Some Field", "Alphanumeric", "Unique")])
        loc = schema["properties"]["_locations"]["properties"]["Some Field"]
        assert loc["type"] == "OBJECT"
        assert set(loc["properties"]) == {"real_page", "shown_page", "section", "document"}


class TestReshapeLocations:
    def _use_group(self, monkeypatch, group, columns):
        """Point schema_builder.get_template at a synthetic row_group template so
        reshape_locations can resolve the group name without a live DB."""
        attrs = [_ta(c, "Numeric", "Multiple", row_group=group) for c in columns]
        tmpl = {"id": "synthetic", "name": "Synthetic", "description": "",
                "group_name": None, "llm_prompt": None, "template_attributes": attrs}
        monkeypatch.setattr("app.schema_builder.get_template", lambda _id: tmpl)

    def test_folds_list_into_dict_keyed_by_row_key(self, monkeypatch):
        self._use_group(monkeypatch, "Financials By Year", ["Financial Statement Date", "Revenue"])
        result = {"_locations": {"Financials By Year": [
            {"_row_key": "31-12-2024",
             "Financial Statement Date": {"real_page": 15, "shown_page": "12", "section": "SOFP", "document": "a.pdf"},
             "Revenue": {"real_page": 16, "shown_page": "13", "section": "P&L", "document": "a.pdf"}},
            {"_row_key": "31-12-2023",
             "Revenue": {"real_page": 16, "shown_page": "13", "section": "P&L", "document": "a.pdf"}},
        ]}}
        locs = reshape_locations(result, "synthetic")["_locations"]["Financials By Year"]
        assert set(locs) == {"31-12-2024", "31-12-2023"}
        assert locs["31-12-2024"]["Revenue"]["real_page"] == 16
        assert "_row_key" not in locs["31-12-2024"]  # consumed as the key, not left in the block

    def test_drops_null_cell_locations(self, monkeypatch):
        self._use_group(monkeypatch, "G", ["A", "B"])
        result = {"_locations": {"G": [{"_row_key": "k", "A": {"real_page": 1}, "B": None}]}}
        locs = reshape_locations(result, "synthetic")["_locations"]["G"]
        assert set(locs["k"]) == {"A"}  # the null B location is dropped, not kept as noise

    def test_missing_row_key_falls_back_to_position(self, monkeypatch):
        self._use_group(monkeypatch, "G", ["A"])
        result = {"_locations": {"G": [{"A": {"real_page": 1}}, {"A": {"real_page": 2}}]}}
        locs = reshape_locations(result, "synthetic")["_locations"]["G"]
        assert set(locs) == {"row_1", "row_2"}

    def test_duplicate_row_keys_are_disambiguated(self, monkeypatch):
        self._use_group(monkeypatch, "G", ["A"])
        result = {"_locations": {"G": [
            {"_row_key": "dup", "A": {"real_page": 1}},
            {"_row_key": "dup", "A": {"real_page": 2}},
        ]}}
        locs = reshape_locations(result, "synthetic")["_locations"]["G"]
        assert set(locs) == {"dup", "dup (2)"}

    def test_noop_when_no_locations(self, monkeypatch):
        self._use_group(monkeypatch, "G", ["A"])
        assert reshape_locations({"mocked": True}, "synthetic") == {"mocked": True}


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

    def test_prompt_includes_global_malay_guidance(self):
        # Appended to every template's prompt (stored or rendered) so Malay/
        # bilingual documents still populate the English-keyed schema.
        for template_id in (COMPANY_ACT_SECTION_14, BANK_STATEMENTS):
            prompt = generate_extraction_prompt(template_id)
            assert "Bahasa Malaysia" in prompt
            assert "Nama Syarikat" in prompt
            assert "keep every output key exactly as named above, in English" in prompt

    def test_unknown_template_raises(self):
        with pytest.raises(TemplateNotFoundError):
            generate_extraction_prompt(UNKNOWN_TEMPLATE_ID)
