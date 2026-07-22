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


class TestBuildSsmCorporateDocsCombinedTemplate:
    """The single 'SSM Business Registration' template (docs/ssm-one-form.md)."""

    def _combined(self, **overrides):
        base = {
            "Document Type": "SSM Business Registration",
            "Entity Name": "ValueCapital Sdn. Bhd.",
            "Business Registration Number": "715023-H",
            "Incorporation Date": "24 Apr 2013",
            "MSIC Code": "46590",
            "Main Business": "Wholesale trade",
            "Registered Address": "23, Jalan Anggerik 4, 81200 Johor Bahru",
            "Directors": [
                {"Director Name": "Rowan Atkinson", "Director NRIC or Passport Number": "550106-12-5821"},
            ],
            "Shareholders": [
                {"Shareholder Name": "Rowan Atkinson", "Shareholder Percentage": "60%",
                 "Shareholder NRIC or Passport Number": "550106-12-5821"},
            ],
        }
        base.update(overrides)
        return {"SSM Business Registration": base}

    def test_one_combined_doc_with_no_subtype(self):
        docs = build_ssm_corporate_docs(self._combined(), entity_type="Sdn Bhd")
        assert len(docs) == 1
        assert docs[0].document_subtype is None
        assert docs[0].document_id == "ssm_business_registration"
        assert docs[0].data.entity_name == "ValueCapital Sdn. Bhd."

    def test_directors_parsed_from_combined_template(self):
        docs = build_ssm_corporate_docs(self._combined(), entity_type="Sdn Bhd")
        assert [p.name for p in docs[0].data.directors] == ["Rowan Atkinson"]
        assert [p.nric_passport for p in docs[0].data.directors] == ["550106-12-5821"]

    def test_shareholders_built_now_that_nric_attribute_exists(self):
        # The combined template carries Shareholder NRIC or Passport Number,
        # so shareholders build (no longer degrade to None) and reach
        # find_missing_ic_documents.
        warnings = []
        docs = build_ssm_corporate_docs(self._combined(), entity_type="Sdn Bhd", warnings=warnings)
        assert [p.name for p in docs[0].data.shareholders] == ["Rowan Atkinson"]
        assert [p.nric_passport for p in docs[0].data.shareholders] == ["550106-12-5821"]
        # The old "no Shareholder NRIC attribute" gap warning is gone.
        assert not any("no Shareholder NRIC" in w.message for w in warnings)

    def test_shareholder_with_null_nric_warns_but_still_builds(self):
        warnings = []
        docs = build_ssm_corporate_docs(
            self._combined(**{"Shareholders": [
                {"Shareholder Name": "No ID Holder", "Shareholder NRIC or Passport Number": None},
            ]}),
            entity_type="Sdn Bhd", warnings=warnings,
        )
        assert docs[0].data.shareholders[0].nric_passport == ""
        assert any("Shareholder NRIC" in w.field for w in warnings)

    def test_complete_combined_doc_raises_no_completeness_warning(self):
        warnings = []
        build_ssm_corporate_docs(self._combined(), entity_type="Sdn Bhd", warnings=warnings)
        # The shareholder-NRIC gap warning is expected; no missing-field warnings.
        fields = {w.field for w in warnings}
        assert "Incorporation Date" not in fields
        assert "Registered Address" not in fields
        assert "Directors" not in fields

    def test_missing_fields_raise_warnings_not_failure(self):
        warnings = []
        docs = build_ssm_corporate_docs(
            self._combined(**{"Incorporation Date": None, "Registered Address": None, "Directors": None}),
            entity_type="Sdn Bhd", warnings=warnings,
        )
        assert len(docs) == 1  # still builds -- warning, not error
        fields = {w.field for w in warnings}
        assert {"Incorporation Date", "Registered Address", "Directors"} <= fields

    def test_null_directors_group_is_tolerated(self):
        docs = build_ssm_corporate_docs(
            self._combined(**{"Directors": None}), entity_type="Sdn Bhd",
        )
        assert docs[0].data.directors == []


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

    def test_maps_available_bank_name_and_preserves_unknown_fields(self, extracted_by_template):
        # This fixture's Bank Statements has no Currency attribute -> null,
        # warned; Account Type has no source attribute at all.
        warnings = []
        doc = build_bank_statement_doc(extracted_by_template, entity_name="X", warnings=warnings)
        assert doc.data.bank_name == "MAYBANK BERHAD"
        assert doc.data.currency is None
        assert doc.data.account_type is None
        assert {w.field for w in warnings} >= {"Currency", "Account Type"}

    def test_reads_myr_currency_without_warning(self, extracted_by_template):
        extracted_by_template["Bank Statements"]["Currency"] = "MYR"
        warnings = []
        doc = build_bank_statement_doc(extracted_by_template, entity_name="X", warnings=warnings)
        assert doc.data.currency == "MYR"
        assert not any(w.field == "Currency" for w in warnings)

    def test_non_myr_currency_is_read_and_warned(self, extracted_by_template):
        extracted_by_template["Bank Statements"]["Currency"] = "SGD"
        warnings = []
        doc = build_bank_statement_doc(extracted_by_template, entity_name="X", warnings=warnings)
        assert doc.data.currency == "SGD"
        assert any(w.field == "Currency" and "not MYR" in w.message for w in warnings)

    def test_records_source_template_provenance(self, extracted_by_template):
        result = build_validation_bundle(
            extracted_by_template,
            entity_type="Sdn Bhd",
        )
        assert result.bundle.extracted_documents
        assert all(doc.provenance is not None for doc in result.bundle.extracted_documents)
        bank = next(
            doc for doc in result.bundle.extracted_documents
            if doc.document_type == "bank_statement"
        )
        assert bank.provenance.source_template == "Bank Statements"

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

    def test_fields_stay_correlated_within_each_director_row(self):
        # Each director is one object in the "Directors" row_group, so name,
        # NRIC and the IC-present flags can't drift apart across parallel
        # arrays -- even when one director is missing a field, only that
        # director's row is affected.
        warnings = []
        docs = build_identity_documents(
            {
                "MyKad (Director ID or Passport)": {
                    "Directors": [
                        {"Director Name": "A", "Director NRIC or Passport Number": "1",
                         "Front Side IC Present": True, "Back Side IC Present": True},
                        {"Director Name": "B", "Director NRIC or Passport Number": None,
                         "Front Side IC Present": True, "Back Side IC Present": False},
                    ],
                }
            },
            warnings=warnings,
        )
        assert [d.data.individual_name for d in docs] == ["A", "B"]
        assert docs[0].data.nric_passport == "1"
        assert docs[1].data.nric_passport == ""  # null flagged, doesn't crash or shift
        assert docs[1].data.back_image_present is False
        assert any(w.document_id == "identity_document_1" and "NRIC" in w.field for w in warnings)


class TestBuildConsentFormDocs:
    def test_one_doc_per_signatory(self, extracted_by_template):
        docs = build_consent_form_docs(extracted_by_template)
        assert len(docs) == 3  # 2 directors + 1 duplicate company-representative signature

    def test_signature_read_from_extraction(self, extracted_by_template):
        # The live template carries a per-signatory "Consent Form Signature"
        # boolean -- the signature value comes straight from extraction.
        docs = build_consent_form_docs(extracted_by_template)
        assert all(d.data.signature_present is True for d in docs)

    def test_null_signature_stays_null_not_false(self, extracted_by_template):
        # null ("not confirmed"), not False ("confirmed unsigned") -- see
        # ConsentFormData.signature_present's docstring.
        extracted_by_template["Consent Form"]["Applicants"][0]["Consent Form Signature"] = None
        docs = build_consent_form_docs(extracted_by_template)
        assert docs[0].data.signature_present is None

    def test_confirmed_unsigned_is_false_not_null(self, extracted_by_template):
        extracted_by_template["Consent Form"]["Applicants"][0]["Consent Form Signature"] = False
        docs = build_consent_form_docs(extracted_by_template)
        assert docs[0].data.signature_present is False

    def test_reads_legacy_directors_row_group_as_fallback(self, extracted_by_template):
        # Older extraction output used a "Directors" row_group with no
        # signature attribute -- still parsed, signature left null.
        consent = extracted_by_template["Consent Form"]
        consent["Directors"] = [{k: v for k, v in row.items() if k != "Consent Form Signature"}
                                for row in consent.pop("Applicants")]
        docs = build_consent_form_docs(extracted_by_template)
        assert len(docs) == 3
        assert all(d.data.signature_present is None for d in docs)


class TestBuildCustomerInformationDoc:
    def test_reads_from_customer_information_form_template(self, extracted_by_template):
        doc = build_customer_information_doc(extracted_by_template)
        assert doc.provenance.source_template == "Customer Information Form"
        assert [d.name for d in doc.data.directors] == [
            "MOHD AIMAN BIN ZULKIFLI", "NURUL AIN BINTI ZULKIFLI",
        ]
        assert doc.data.company_office_status == "Rented"

    def test_director_particulars_stay_row_aligned(self, extracted_by_template):
        extracted_by_template["Customer Information Form"]["Directors"] = [
            {"Director Name": "AIMAN", "Director Email Address": "aiman@x.my"},
            {"Director Name": "NURUL", "Director Email Address": "nurul@x.my"},
        ]
        doc = build_customer_information_doc(extracted_by_template)
        assert [d.name for d in doc.data.directors] == ["AIMAN", "NURUL"]
        assert [d.email for d in doc.data.directors] == ["aiman@x.my", "nurul@x.my"]

    def test_null_fields_coerced_to_blank_not_warned_per_field(self, extracted_by_template):
        extracted_by_template["Customer Information Form"]["Company Office Status"] = None
        warnings = []
        doc = build_customer_information_doc(extracted_by_template, warnings=warnings)
        assert doc.data.company_office_status == ""  # completeness rule reports it, adapter doesn't warn
        assert not any(w.field == "Company Office Status" for w in warnings)

    def test_missing_template_returns_none(self):
        assert build_customer_information_doc({}) is None


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
        extracted_by_template["Consent Form"]["Applicants"][0]["Director NRIC or Passport Number"] = None
        warnings = []
        docs = build_consent_form_docs(extracted_by_template, warnings=warnings)
        assert docs[0].data.nric_passport == ""  # doesn't crash
        assert any(
            w.document_id == "consent_form_0" and "NRIC" in w.field
            for w in warnings
        )

class TestBuildValidationBundle:
    def test_full_bundle_builds_and_passes_every_check(self, extracted_by_template):
        result = build_validation_bundle(
            extracted_by_template,
            bundle_id="BUNDLE-TEST-1", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd",
        )
        report = ValidationEngine().run(result.bundle)
        assert report.overall_passed is True

    def test_clean_extraction_produces_only_the_known_pre_existing_gaps(self, extracted_by_template):
        # The example fixture is otherwise complete -- the only warnings
        # should be the pre-documented extraction schema gaps: no
        # Shareholder NRIC attribute on SSM Form 24, no Business Registration
        # Number attribute on SSM Form 44, and no currency/account-type
        # attributes on Bank Statements.
        # signature_present is supplied explicitly here, so no warning for it.
        result = build_validation_bundle(
            extracted_by_template,
            bundle_id="BUNDLE-TEST-1B", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd",
        )
        fields = {w.field for w in result.warnings}
        assert fields == {
            "Shareholders", "Business Registration Number", "Currency", "Account Type",
            "audited",
        }

    def test_document_types_present_matches_actual_documents(self, extracted_by_template):
        result = build_validation_bundle(
            extracted_by_template,
            bundle_id="BUNDLE-TEST-2", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd",
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
            entity_type="Sdn Bhd",
        )
        assert result.bundle.metadata.document_types_present == ["ssm_corporate_form"]
        report = ValidationEngine().run(result.bundle)
        # SSM completeness is no longer a check; the bundle still builds and the
        # remaining rules just skip the document types that aren't present.
        assert report.overall_passed is True

    def test_entity_name_propagates_from_ssm_to_financial_and_bank_docs(self, extracted_by_template):
        result = build_validation_bundle(
            extracted_by_template,
            bundle_id="BUNDLE-TEST-3", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd",
        )
        financial_docs = [d for d in result.bundle.extracted_documents if d.document_type == "financial_statement"]
        bank_docs = [d for d in result.bundle.extracted_documents if d.document_type == "bank_statement"]
        assert all(d.data.entity_name == "ALPHA TECH SOLUTIONS SDN BHD" for d in financial_docs)
        assert all(d.data.entity_name == "ALPHA TECH SOLUTIONS SDN BHD" for d in bank_docs)

    def test_a_null_deep_in_the_extraction_result_does_not_crash_the_whole_bundle(self, extracted_by_template):
        # null balance on Feb's month-end transaction row -> _safe_float warns, no crash
        extracted_by_template["Bank Statements"]["Transactions"][2]["Transaction Balance"] = None
        result = build_validation_bundle(
            extracted_by_template,
            bundle_id="BUNDLE-TEST-4", system_date=date(2026, 7, 7),
            entity_type="Sdn Bhd",
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

    def test_entity_type_warns_and_defaults_when_not_supplied(self, extracted_by_template):
        # entity_type has no extraction source (Application Details was dropped);
        # omitting it defaults to "" with a warning, never a crash.
        result = build_validation_bundle(extracted_by_template)
        ssm_docs = [d for d in result.bundle.extracted_documents if d.document_type == "ssm_corporate_form"]
        assert all(d.data.entity_type == "" for d in ssm_docs)
        assert any(w.field == "entity_type" for w in result.warnings)
        report = ValidationEngine().run(result.bundle)
        assert report is not None  # still produced a complete report

    def test_signature_read_from_extraction_when_not_supplied(self, extracted_by_template):
        # No signature_present override -> each consent doc's signature comes
        # from the extracted "Consent Form Signature" boolean (all true here).
        result = build_validation_bundle(extracted_by_template)
        assert not any(w.field == "signature_present" for w in result.warnings)
        consent_docs = [d for d in result.bundle.extracted_documents if d.document_type == "consent_form"]
        assert all(d.data.signature_present is True for d in consent_docs)
