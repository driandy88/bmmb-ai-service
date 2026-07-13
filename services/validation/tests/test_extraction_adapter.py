"""
Tests for extraction_adapter.py -- the mapping from raw extraction results
(services.extraction's POST /extract output shape) into a ValidationBundle.
Uses examples/extraction_results_example.json as its main fixture: a full,
realistic set of extraction results for one application (same Alpha Tech
Solutions Sdn Bhd persona as examples/sample_bundle_passing.json), so the
adapter is tested against something that looks like real extraction output,
not a hand-trimmed synthetic dict.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from services.validation.extraction_adapter import (
    AdapterDataGapError,
    build_bank_statement_doc,
    build_consent_form_docs,
    build_customer_information_doc,
    build_financial_statement_docs,
    build_identity_documents,
    build_ssm_corporate_docs,
    build_validation_bundle,
)
from services.validation.engine import ValidationEngine

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def extracted_by_template() -> dict:
    raw = json.loads((EXAMPLES_DIR / "extraction_results_example.json").read_text())
    raw.pop("_comment", None)
    return raw


class TestBuildSsmCorporateDocs:
    def test_one_doc_per_ssm_template_present(self, extracted_by_template):
        docs = build_ssm_corporate_docs(extracted_by_template, entity_type="Sdn Bhd")
        subtypes = {d.document_subtype for d in docs}
        assert subtypes == {"form_24", "form_44", "form_49"}

    def test_entity_type_comes_from_override_not_extraction(self, extracted_by_template):
        docs = build_ssm_corporate_docs(extracted_by_template, entity_type="Sdn Bhd")
        assert all(d.data.entity_type == "Sdn Bhd" for d in docs)

    def test_directors_built_from_form_49(self, extracted_by_template):
        docs = build_ssm_corporate_docs(extracted_by_template, entity_type="Sdn Bhd")
        form_49 = next(d for d in docs if d.document_subtype == "form_49")
        assert {p.name for p in form_49.data.directors} == {
            "MOHD AIMAN BIN ZULKIFLI", "NURUL AIN BINTI ZULKIFLI",
        }

    def test_shareholders_are_none_pending_nric_attribute_gap(self, extracted_by_template):
        # SSM Form 24 has Shareholder Name but no Shareholder NRIC attribute
        # (see extraction_adapter.py's module docstring) -- shareholders
        # can't be built without one, so this degrades to None rather than
        # emitting a blank/wrong NRIC.
        docs = build_ssm_corporate_docs(extracted_by_template, entity_type="Sdn Bhd")
        form_24 = next(d for d in docs if d.document_subtype == "form_24")
        assert form_24.data.shareholders is None

    def test_missing_ssm_templates_produce_no_docs(self):
        docs = build_ssm_corporate_docs({}, entity_type="Sdn Bhd")
        assert docs == []


class TestBuildFinancialStatementDocs:
    def test_one_doc_per_year_column(self, extracted_by_template):
        docs = build_financial_statement_docs(extracted_by_template, entity_name="ALPHA TECH SOLUTIONS SDN BHD")
        assert {d.data.financial_year_end for d in docs} == {date(2025, 12, 31), date(2024, 12, 31)}

    def test_section_flags_shared_across_years(self, extracted_by_template):
        docs = build_financial_statement_docs(extracted_by_template, entity_name="ALPHA TECH SOLUTIONS SDN BHD")
        assert all(d.data.balance_sheet_present for d in docs)
        assert all(d.data.auditors_report_present for d in docs)

    def test_date_format_is_converted_from_ddmmyyyy(self, extracted_by_template):
        # Extraction emits "31-12-2025"; ValidationBundle needs a real date.
        docs = build_financial_statement_docs(extracted_by_template, entity_name="X")
        assert date(2025, 12, 31) in {d.data.financial_year_end for d in docs}

    def test_missing_template_produces_no_docs(self):
        assert build_financial_statement_docs({}, entity_name="X") == []


class TestBuildBankStatementDoc:
    def test_date_range_spans_min_to_max_month(self, extracted_by_template):
        doc = build_bank_statement_doc(extracted_by_template, entity_name="ALPHA TECH SOLUTIONS SDN BHD")
        assert doc.data.statement_start_date == date(2026, 1, 1)
        assert doc.data.statement_end_date == date(2026, 6, 30)

    def test_monthly_balances_carried_through(self, extracted_by_template):
        doc = build_bank_statement_doc(extracted_by_template, entity_name="X")
        assert len(doc.data.monthly_balances) == 6
        assert doc.data.monthly_balances[0].month == "January 2026"
        assert doc.data.monthly_balances[0].end_balance == 42500.00

    def test_missing_template_returns_none(self):
        assert build_bank_statement_doc({}, entity_name="X") is None


class TestBuildIdentityDocuments:
    def test_one_doc_per_director(self, extracted_by_template):
        docs = build_identity_documents(extracted_by_template)
        assert len(docs) == 2
        assert {d.data.individual_name for d in docs} == {
            "MOHD AIMAN BIN ZULKIFLI", "NURUL AIN BINTI ZULKIFLI",
        }

    def test_front_and_back_present_correlated_correctly(self, extracted_by_template):
        docs = build_identity_documents(extracted_by_template)
        assert all(d.data.front_image_present and d.data.back_image_present for d in docs)

    def test_expiry_date_always_none_pending_attribute_gap(self, extracted_by_template):
        docs = build_identity_documents(extracted_by_template)
        assert all(d.data.expiry_date is None for d in docs)

    def test_mismatched_array_lengths_degrade_gracefully_with_a_warning(self):
        # Does NOT raise: truncates to the shortest array so the bundle
        # still builds, and records exactly what was found (current_state)
        # vs what was expected (expected_state) for the AI review step.
        warnings = []
        docs = build_identity_documents(
            {
                "MyKad (Director ID or Passport)": {
                    "Director Name": ["A", "B"],
                    "Director NRIC or Passport Number": ["1"],
                    "Front Side IC Present": [True, True],
                    "Back Side IC Present": [True, True],
                }
            },
            warnings=warnings,
        )
        assert len(docs) == 1  # truncated to the shortest (NRIC) array
        assert docs[0].data.individual_name == "A"

        mismatch = next(w for w in warnings if "Director Name" in w.field)
        assert "Director Name=2" in mismatch.current_state
        assert "Director NRIC or Passport Number=1" in mismatch.current_state
        assert "same length" in mismatch.expected_state


class TestBuildConsentFormDocs:
    def test_one_doc_per_signatory(self, extracted_by_template):
        docs = build_consent_form_docs(extracted_by_template, signature_present=True)
        assert len(docs) == 3  # 2 directors + 1 duplicate company-representative signature

    def test_signature_present_stays_null_when_not_supplied(self, extracted_by_template):
        # null ("not confirmed"), not False ("confirmed unsigned") -- see
        # ConsentFormData.signature_present's docstring.
        docs = build_consent_form_docs(extracted_by_template)
        assert all(d.data.signature_present is None for d in docs)

    def test_signature_present_override_applied_to_all(self, extracted_by_template):
        docs = build_consent_form_docs(extracted_by_template, signature_present=True)
        assert all(d.data.signature_present is True for d in docs)


class TestBuildCustomerInformationDoc:
    def test_maps_application_details_not_customer_information_form(self, extracted_by_template):
        doc = build_customer_information_doc(
            extracted_by_template, tenure_months=60, repayment_frequency="Monthly",
        )
        assert doc.data.main_contact_names == ["MOHD AIMAN BIN ZULKIFLI"]
        assert doc.data.financing_amount == 500000.00
        assert doc.data.product_type == "SME Term Financing"

    def test_missing_tenure_or_frequency_defaults_and_warns(self, extracted_by_template):
        warnings = []
        doc = build_customer_information_doc(extracted_by_template, warnings=warnings)
        assert doc.data.tenure_months == 0
        assert doc.data.repayment_frequency == "Unknown"
        assert {w.field for w in warnings} >= {"tenure_months", "repayment_frequency"}

    def test_missing_template_returns_none(self):
        assert build_customer_information_doc({}, tenure_months=1, repayment_frequency="Monthly") is None


class TestNullValuesCrossTheAdapterSafely:
    """extraction's own prompt convention is 'return null for any field not
    found' -- a null value is an entirely expected, routine extraction
    result, not a malformed one. It must not crash the adapter."""

    def test_null_entity_name_does_not_crash_and_is_flagged(self, extracted_by_template):
        extracted_by_template["SSM Form 44"]["Entity Name"] = None
        warnings = []
        docs = build_ssm_corporate_docs(extracted_by_template, entity_type="Sdn Bhd", warnings=warnings)
        form_44 = next(d for d in docs if d.document_subtype == "form_44")
        assert form_44.data.entity_name == ""  # conservative placeholder, not a crash

        warning = next(w for w in warnings if w.document_id == "ssm_form_44" and w.field == "Entity Name")
        assert warning.current_state == "null (not extracted / not found on the document)"
        assert "non-empty string" in warning.expected_state

    def test_null_boolean_flag_stays_null_and_is_flagged(self, extracted_by_template):
        # null ("couldn't determine"), not False ("confirmed absent") -- see
        # FinancialStatementData's docstring.
        extracted_by_template["Financial Statements (Sdn Bhd)"]["Auditor's Report Present"] = None
        warnings = []
        docs = build_financial_statement_docs(
            extracted_by_template, entity_name="X", warnings=warnings,
        )
        assert all(d.data.auditors_report_present is None for d in docs)
        assert any(w.field == "Auditor's Report Present" for w in warnings)

    def test_null_element_within_an_array_is_flagged_per_row(self, extracted_by_template):
        extracted_by_template["Consent Form"]["Director NRIC or Passport Number"][0] = None
        warnings = []
        docs = build_consent_form_docs(extracted_by_template, signature_present=True, warnings=warnings)
        assert docs[0].data.nric_passport == ""  # doesn't crash
        assert any(
            w.document_id == "consent_form_0" and "NRIC" in w.field
            for w in warnings
        )

    def test_null_financing_amount_defaults_to_zero_and_is_flagged(self, extracted_by_template):
        extracted_by_template["Application Details"]["Proposed Financing Amount"] = None
        warnings = []
        doc = build_customer_information_doc(
            extracted_by_template, tenure_months=60, repayment_frequency="Monthly", warnings=warnings,
        )
        assert doc.data.financing_amount == 0.0
        assert any(w.field == "Proposed Financing Amount" for w in warnings)


class TestBuildValidationBundle:
    def test_full_bundle_builds_and_passes_every_check(self, extracted_by_template):
        result = build_validation_bundle(
            extracted_by_template,
            bundle_id="BUNDLE-TEST-1", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd", tenure_months=60, repayment_frequency="Monthly",
            signature_present=True,
        )
        report = ValidationEngine().run(result.bundle)
        assert report.overall_passed is True

    def test_clean_extraction_produces_only_the_known_pre_existing_gaps(self, extracted_by_template):
        # The example fixture is otherwise complete -- the only warnings
        # should be the pre-documented extraction schema gaps: no
        # Shareholder NRIC attribute on SSM Form 24, and no Business
        # Registration Number attribute on SSM Form 44 (confirmed against
        # the real bmmb_dev wiring -- Form 44 genuinely doesn't capture it).
        # signature_present is supplied explicitly here, so no warning for it.
        result = build_validation_bundle(
            extracted_by_template,
            bundle_id="BUNDLE-TEST-1B", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd", tenure_months=60, repayment_frequency="Monthly",
            signature_present=True,
        )
        fields = {w.field for w in result.warnings}
        assert fields == {"Shareholder Name", "Business Registration Number"}

    def test_document_types_present_matches_actual_documents(self, extracted_by_template):
        result = build_validation_bundle(
            extracted_by_template,
            bundle_id="BUNDLE-TEST-2", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd", tenure_months=60, repayment_frequency="Monthly",
        )
        assert set(result.bundle.metadata.document_types_present) == {
            d.document_type for d in result.bundle.extracted_documents
        }

    def test_missing_extraction_results_produce_a_smaller_but_valid_bundle(self):
        # Only an SSM form available -- should not crash; the missing
        # document types just aren't in the bundle (ValidationEngine treats
        # that as "skip", not "fail").
        result = build_validation_bundle(
            {
                "SSM Form 24": {
                    "Entity Name": "SOLO SDN BHD",
                    "Business Registration Number": "202301000001",
                },
            },
            bundle_id="BUNDLE-PARTIAL", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd", tenure_months=0, repayment_frequency="Monthly",
        )
        assert result.bundle.metadata.document_types_present == ["ssm_corporate_form"]
        report = ValidationEngine().run(result.bundle)
        ssm_check = next(r for r in report.results if r.check == "verify_ssm_completeness")
        assert ssm_check.passed is False  # only form_24, not the full 24+44+49 set

    def test_entity_name_propagates_from_ssm_to_financial_and_bank_docs(self, extracted_by_template):
        result = build_validation_bundle(
            extracted_by_template,
            bundle_id="BUNDLE-TEST-3", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd", tenure_months=60, repayment_frequency="Monthly",
        )
        financial_docs = [d for d in result.bundle.extracted_documents if d.document_type == "financial_statement"]
        bank_docs = [d for d in result.bundle.extracted_documents if d.document_type == "bank_statement"]
        assert all(d.data.entity_name == "ALPHA TECH SOLUTIONS SDN BHD" for d in financial_docs)
        assert all(d.data.entity_name == "ALPHA TECH SOLUTIONS SDN BHD" for d in bank_docs)

    def test_a_null_deep_in_the_extraction_result_does_not_crash_the_whole_bundle(self, extracted_by_template):
        extracted_by_template["Bank Statements"]["Monthly End Balance"][2] = None
        result = build_validation_bundle(
            extracted_by_template,
            bundle_id="BUNDLE-TEST-4", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd", tenure_months=60, repayment_frequency="Monthly",
        )
        assert result.bundle is not None
        assert any(w.document_type == "bank_statement" for w in result.warnings)


class TestBuildValidationBundleWithNoOverrides:
    """extracted_by_template is the only required argument -- a raw
    extraction results dump, completely unmodified, must be a valid call
    on its own (this is the whole point of softening AdapterDataGapError
    into warnings for these fields)."""

    def test_bare_extraction_dump_builds_successfully(self, extracted_by_template):
        result = build_validation_bundle(extracted_by_template)
        assert result.bundle.bundle_id  # auto-generated
        assert result.bundle.metadata.system_date == date.today()
        assert len(result.bundle.extracted_documents) > 0

    def test_entity_type_is_read_from_application_details(self, extracted_by_template):
        result = build_validation_bundle(extracted_by_template)
        ssm_docs = [d for d in result.bundle.extracted_documents if d.document_type == "ssm_corporate_form"]
        assert all(d.data.entity_type == "Sdn Bhd" for d in ssm_docs)
        # No warning for entity_type since it was derivable from Application
        # Details' "Business Entity Type", unlike tenure_months/repayment_frequency.
        assert not any(w.field == "entity_type" for w in result.warnings)

    def test_missing_tenure_and_repayment_frequency_are_warned_not_raised(self, extracted_by_template):
        result = build_validation_bundle(extracted_by_template)
        fields = {w.field for w in result.warnings}
        assert "tenure_months" in fields
        assert "repayment_frequency" in fields
        report = ValidationEngine().run(result.bundle)
        assert report is not None  # still produced a complete report

    def test_signature_present_stays_null_and_is_warned(self, extracted_by_template):
        result = build_validation_bundle(extracted_by_template)
        assert any(w.field == "signature_present" for w in result.warnings)
        consent_docs = [d for d in result.bundle.extracted_documents if d.document_type == "consent_form"]
        assert all(d.data.signature_present is None for d in consent_docs)
