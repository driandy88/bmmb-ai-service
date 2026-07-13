"""
Tests for examples/buggy_adapter_demo.py: raw-extraction -> ValidationBundle
mapping. This is the deliberately-bugged teaching fixture, NOT the real
extraction adapter (see extraction_adapter.py for that).

_adapt_ssm_corporate_form is tested for correct mapping. _adapt_consent_form
has a *known, deliberate* bug (see buggy_adapter_demo.py's module docstring
and examples/test_conflict_example.py) — the test below characterizes that
existing behavior so a future fix is a deliberate, visible diff here rather
than a silent regression either way.
"""

import pytest

from services.validation.examples.buggy_adapter_demo import adapt_raw_extraction


class TestAdaptSsmCorporateForm:
    def test_maps_entity_and_shareholders(self, raw_extraction_conflict):
        bundle = adapt_raw_extraction(raw_extraction_conflict)
        ssm_doc = next(d for d in bundle.extracted_documents if d.document_type == "ssm_corporate_form")

        assert ssm_doc.data.entity_name == "ALPHA TECH SOLUTIONS SDN BHD"
        assert ssm_doc.data.business_registration_number == "202301098765"
        assert ssm_doc.data.shareholders[0].name == "MOHD AIMAN BIN ZULKIFLI"
        assert ssm_doc.data.shareholders[0].nric_passport == "880214-14-5123"

    def test_unregistered_document_type_raises(self):
        raw = {
            "bundle_id": "B1",
            "system_date": "2026-07-08",
            "raw_documents": [{"document_id": "d1", "document_type": "bank_statement", "raw_fields": {}}],
        }
        with pytest.raises(ValueError, match="No adapter registered"):
            adapt_raw_extraction(raw)


class TestAdaptConsentFormKnownBug:
    """Characterizes buggy_adapter_demo.py's documented _adapt_consent_form bug.

    Once this is fixed, this test should be rewritten to assert the correct
    mapping (entity_name stays the entity; individual_name/nric_passport are
    populated from "Authorized Names"/"Authorized NRICs") instead of deleted.
    """

    def test_authorized_names_clobbers_entity_name(self, raw_extraction_conflict):
        bundle = adapt_raw_extraction(raw_extraction_conflict)
        consent_doc = next(d for d in bundle.extracted_documents if d.document_type == "consent_form")

        # BUG: this should be "ALPHA TECH SOLUTIONS SDN BHD" (the entity),
        # not the signatory's name.
        assert consent_doc.data.entity_name == "MOHD AIMAN BIN ZULKIFLI"

    def test_individual_name_and_nric_are_left_blank(self, raw_extraction_conflict):
        bundle = adapt_raw_extraction(raw_extraction_conflict)
        consent_doc = next(d for d in bundle.extracted_documents if d.document_type == "consent_form")

        # BUG: "Authorized Names"/"Authorized NRICs" are never mapped here,
        # even though the raw extraction has them.
        assert consent_doc.data.individual_name == ""
        assert consent_doc.data.nric_passport == ""

    def test_signature_flag_is_still_mapped_correctly(self, raw_extraction_conflict):
        bundle = adapt_raw_extraction(raw_extraction_conflict)
        consent_doc = next(d for d in bundle.extracted_documents if d.document_type == "consent_form")
        assert consent_doc.data.signature_present is True
