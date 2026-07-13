"""
Integration tests for ValidationEngine.run() — checks that the engine wires
rules/ together correctly against a full ValidationBundle, using the
examples/ sample bundles as fixtures (see conftest.py). Purely deterministic:
no Gemini, no network, no GCP credentials required.
"""

from services.validation.bundle import ValidationBundle
from services.validation.engine import ValidationEngine


def _run(raw: dict):
    bundle = ValidationBundle(**raw)
    return ValidationEngine().run(bundle)


class TestPassingBundle:
    def test_overall_passed(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        assert report.overall_passed is True

    def test_entity_name_and_type_taken_from_ssm_form(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        assert report.entity_name == "ALPHA TECH SOLUTIONS SDN BHD"
        assert report.entity_type == "Sdn Bhd"

    def test_no_check_explicitly_fails(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        failed = [r.check for r in report.results if r.passed is False]
        assert failed == []


class TestFailingBundle:
    def test_overall_failed(self, failing_bundle_raw):
        report = _run(failing_bundle_raw)
        assert report.overall_passed is False

    def test_missing_consent_form_is_caught(self, failing_bundle_raw):
        report = _run(failing_bundle_raw)
        consent_check = next(r for r in report.results if r.check == "verify_consent_signatures")
        assert consent_check.passed is False
        assert consent_check.details["missing_consent"]


class TestSkippedChecksForIncompleteBundles:
    def test_missing_document_types_are_skipped_not_failed(self):
        raw = {
            "bundle_id": "BUNDLE-MINIMAL",
            "metadata": {
                "total_documents_received": 1,
                "system_date": "2026-07-08",
                "document_types_present": ["ssm_corporate_form"],
            },
            "extracted_documents": [
                {
                    "document_id": "doc_1",
                    "document_type": "ssm_corporate_form",
                    "document_subtype": "form_24",
                    "data": {
                        "entity_name": "SOLO SDN BHD",
                        "business_registration_number": "202301000001",
                        "entity_type": "Sdn Bhd",
                    },
                }
            ],
        }
        report = _run(raw)

        # SSM completeness is a real check (fails: only form_24, not 24+44+49)
        ssm_check = next(r for r in report.results if r.check == "verify_ssm_completeness")
        assert ssm_check.passed is False

        # Everything that needs a document type absent from this bundle is
        # skipped (passed=None), not silently marked as failed.
        for check_name in (
            "calculate_financial_18_month_rule",
            "check_bank_statement_continuity",
            "verify_bank_statement_duration",
            "check_ic_front_and_back",
            "find_missing_ic_documents",
            "verify_consent_signatures",
            "validate_form_d_expiry",
        ):
            check = next(r for r in report.results if r.check == check_name)
            assert check.passed is None

        # A skipped check never flips overall_passed to False on its own.
        assert report.overall_passed is False  # only because of the real ssm_check failure above

    def test_empty_bundle_produces_only_skips(self):
        raw = {
            "bundle_id": "BUNDLE-EMPTY",
            "metadata": {
                "total_documents_received": 0,
                "system_date": "2026-07-08",
                "document_types_present": [],
            },
            "extracted_documents": [],
        }
        report = _run(raw)
        assert report.overall_passed is True
        assert all(r.passed is None for r in report.results)
        assert report.entity_name == ""
        assert report.entity_type == ""


class TestCrossDocumentMatching:
    def test_entity_name_mismatch_on_bank_statement_is_caught(self, passing_bundle_raw):
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], entity_name="A COMPLETELY DIFFERENT ENTITY BHD"))
            if doc["document_type"] == "bank_statement"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        # engine.py always records these under the "strict_match_entity_names[...]"
        # check name, even when the strict match fails over to fuzzy_match_entity_names
        # internally (see engine.py's cross-matching loop) — the name doesn't change.
        mismatch_checks = [r for r in report.results if r.check.startswith("strict_match_entity_names[")]
        assert mismatch_checks
        assert any(r.passed is False for r in mismatch_checks)
        assert report.overall_passed is False


class TestNewRulesWiring:
    def test_overdraft_is_caught(self, passing_bundle_raw):
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], monthly_balances=[
                {"month": "January 2026", "end_balance": -100.0},
            ]))
            if doc["document_id"] == "doc_004a"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        check = next(r for r in report.results if r.check == "check_bank_statement_overdraft")
        assert check.passed is False
        assert report.overall_passed is False

    def test_stale_bank_statement_is_caught(self, passing_bundle_raw):
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], statement_end_date="2026-01-31"))
            if doc["document_id"] == "doc_004b"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        check = next(r for r in report.results if r.check == "check_bank_statement_freshness")
        assert check.passed is False

    def test_consent_form_count_below_directors_plus_one_is_caught(self, passing_bundle_raw):
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            doc for doc in raw["extracted_documents"] if doc["document_id"] != "doc_006c"
        ]
        report = _run(raw)
        check = next(r for r in report.results if r.check == "verify_consent_form_count")
        assert check.passed is False
        assert check.details["required_count"] == 3
        assert check.details["consent_form_count"] == 2

    def test_missing_application_details_field_is_caught(self, passing_bundle_raw):
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], main_contact_emails=[]))
            if doc["document_type"] == "customer_information"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        check = next(r for r in report.results if r.check == "verify_application_details_completeness")
        assert check.passed is False


class TestTaxDeclarationAlternatePath:
    def _sole_prop_bundle(self, fye_dates):
        return {
            "bundle_id": "BUNDLE-SOLE-PROP",
            "metadata": {
                "total_documents_received": 1 + len(fye_dates),
                "system_date": "2026-07-07",
                "document_types_present": ["ssm_corporate_form", "tax_declaration"],
            },
            "extracted_documents": [
                {
                    "document_id": "doc_ssm",
                    "document_type": "ssm_corporate_form",
                    "document_subtype": "form_b",
                    "data": {
                        "entity_name": "SOLO TRADING",
                        "business_registration_number": "SP0012345",
                        "entity_type": "Sole Proprietor",
                    },
                },
                *[
                    {
                        "document_id": f"doc_tax_{i}",
                        "document_type": "tax_declaration",
                        "data": {"entity_name": "SOLO TRADING", "financial_year_end": fye},
                    }
                    for i, fye in enumerate(fye_dates)
                ],
            ],
        }

    def test_two_consecutive_years_of_tax_declarations_pass(self):
        report = _run(self._sole_prop_bundle(["2024-12-31", "2025-12-31"]))
        consecutive = next(r for r in report.results if r.check == "check_financial_consecutive_years")
        eighteen_month = next(r for r in report.results if r.check == "calculate_financial_18_month_rule")
        assert consecutive.passed is True
        assert eighteen_month.passed is True

    def test_financial_sections_check_is_skipped_for_tax_declarations(self):
        report = _run(self._sole_prop_bundle(["2024-12-31", "2025-12-31"]))
        sections_check = next(r for r in report.results if r.check == "verify_financial_sections_present")
        assert sections_check.passed is None

    def test_gap_year_tax_declarations_fail(self):
        report = _run(self._sole_prop_bundle(["2023-12-31", "2025-12-31"]))
        consecutive = next(r for r in report.results if r.check == "check_financial_consecutive_years")
        assert consecutive.passed is False
