"""
Integration tests for ValidationEngine.run() — checks that the engine wires
rules/ together correctly against a full ValidationBundle, using the
examples/ sample bundles as fixtures (see conftest.py). Purely deterministic:
no Gemini, no network, no GCP credentials required.
"""

from services.validation.bundle import ValidationBundle
from services.validation.engine import ValidationEngine, ValidationStatus
from services.validation.rules import RULE_CATALOG, validate_rule_result
from services.validation.domain.policies import ValidationPolicy


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

    def test_results_have_stable_rule_ids_and_explicit_status(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)

        ssm = next(r for r in report.results if r.check == "verify_ssm_completeness")
        assert ssm.rule_id == "ssm.document_completeness"
        assert ssm.status is ValidationStatus.PASSED

        entity_match = next(
            r for r in report.results
            if r.check.startswith("strict_match_entity_names[")
        )
        assert entity_match.rule_id == "entity_name.match"

    def test_overall_status_is_passed(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        assert report.overall_status is ValidationStatus.PASSED
        assert report.policy_id == "bmmb-sme-2026-01"

    def test_custom_policy_is_recorded_and_used(self, passing_bundle_raw):
        bundle = ValidationBundle(**passing_bundle_raw)
        policy = ValidationPolicy(
            policy_id="test-policy",
            required_ssm_forms_by_entity={"sdn bhd": {"form_24"}},
            default_required_ssm_forms={"form_24"},
            minimum_bank_statement_months_by_entity={"sdn bhd": 6},
            default_minimum_bank_statement_months=6,
            financial_statement_max_age_months=24,
            bank_statement_max_age_months=3,
            required_application_fields={"main_contact_names"},
        )
        report = ValidationEngine(policy=policy).run(bundle)
        assert report.policy_id == "test-policy"
        assert report.overall_passed is True

    def test_rule_catalog_has_unique_stable_ids(self):
        rule_ids = [definition.rule_id for definition in RULE_CATALOG]
        check_names = [definition.check_name for definition in RULE_CATALOG]
        assert len(rule_ids) == len(set(rule_ids))
        assert len(check_names) == len(set(check_names))

    def test_rule_result_contract_rejects_malformed_results(self):
        valid = validate_rule_result({"passed": True, "message": "ok", "details": {}})
        assert valid["passed"] is True

        import pytest

        with pytest.raises(ValueError, match="missing required field"):
            validate_rule_result({"passed": True, "message": "ok"})


class TestResultsByDocument:
    def test_every_result_is_grouped_exactly_once(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        grouped = report.results_by_document
        regrouped_total = sum(len(results) for results in grouped.values())
        assert regrouped_total == len(report.results)

    def test_ssm_completeness_and_cross_document_matching_share_ssm_group(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        ssm_group_checks = {r.check for r in report.results_by_document["SSM_CORPORATE_FORM"]}
        assert "verify_ssm_completeness" in ssm_group_checks
        assert any(check.startswith("strict_match_entity_names[") for check in ssm_group_checks)
        assert any(check.startswith("strict_match_ic_numbers[") for check in ssm_group_checks)

    def test_bank_statement_rules_are_grouped_together(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        bank_group_checks = {r.check for r in report.results_by_document["BANK_STATEMENT"]}
        assert bank_group_checks == {
            "check_bank_statement_continuity",
            "verify_bank_statement_duration",
            "check_bank_statement_freshness",
            "check_bank_statement_overdraft",
            "check_bank_statement_bank_consistency",
            "check_bank_statement_currency",
        }

    def test_grouping_is_included_in_json_output(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        dumped = report.model_dump(mode="json")
        assert "results_by_document" in dumped
        assert set(dumped["results_by_document"]) == {
            "SSM_CORPORATE_FORM", "FINANCIAL_STATEMENT", "BANK_STATEMENT",
            "IDENTITY_DOCUMENT", "CONSENT_FORM", "APPLICATION",
        }

    def test_every_catalog_rule_has_a_document_group(self):
        for definition in RULE_CATALOG:
            assert definition.document_group


class TestFailingBundle:
    def test_overall_failed(self, failing_bundle_raw):
        report = _run(failing_bundle_raw)
        assert report.overall_passed is False

    def test_missing_consent_form_is_caught(self, failing_bundle_raw):
        report = _run(failing_bundle_raw)
        consent_check = next(r for r in report.results if r.check == "verify_consent_signatures")
        assert consent_check.passed is False
        assert consent_check.status is ValidationStatus.FAILED
        assert consent_check.details["missing_consent"]

    def test_failed_check_wins_over_needs_review(self, passing_bundle_raw):
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], back_image_present=None))
            if doc["document_type"] == "identity_document"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        assert report.overall_status is ValidationStatus.NEEDS_REVIEW

        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], back_image_present=False))
            if doc["document_type"] == "identity_document"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        assert report.overall_status is ValidationStatus.FAILED


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
        ):
            check = next(r for r in report.results if r.check == check_name)
            assert check.passed is None
            assert check.status is ValidationStatus.NOT_APPLICABLE

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

    def test_mixed_bank_statement_banks_is_caught(self, passing_bundle_raw):
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], bank_name="CIMB BANK BERHAD"))
            if doc["document_id"] == "doc_004b"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        check = next(r for r in report.results if r.check == "check_bank_statement_bank_consistency")
        assert check.passed is False
        assert report.overall_passed is False

    def test_missing_bank_name_needs_review_not_fail(self, passing_bundle_raw):
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], bank_name=None))
            if doc["document_id"] == "doc_004a"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        check = next(r for r in report.results if r.check == "check_bank_statement_bank_consistency")
        assert check.passed is None
        assert check.status is ValidationStatus.NEEDS_REVIEW
        assert report.overall_passed is True  # None never flips overall_passed
        assert report.overall_status is ValidationStatus.NEEDS_REVIEW

    def test_non_myr_currency_needs_review_not_fail(self, passing_bundle_raw):
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], currency="SGD"))
            if doc["document_id"] == "doc_004b"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        check = next(r for r in report.results if r.check == "check_bank_statement_currency")
        assert check.passed is None
        assert check.status is ValidationStatus.NEEDS_REVIEW
        assert report.overall_passed is True  # a warning never flips overall_passed
        assert report.overall_status is ValidationStatus.NEEDS_REVIEW

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
