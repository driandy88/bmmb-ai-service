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

        freshness = next(r for r in report.results if r.check == "calculate_financial_18_month_rule")
        assert freshness.rule_id == "financial_statement.freshness"
        assert freshness.status is ValidationStatus.PASSED

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
            minimum_bank_statement_months_by_entity={"sdn bhd": 6},
            default_minimum_bank_statement_months=6,
            financial_statement_max_age_months=24,
            bank_statement_max_age_months=3,
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

    def test_cross_document_matching_is_grouped_under_ssm(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        ssm_group_checks = {r.check for r in report.results_by_document["SSM_CORPORATE_FORM"]}
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
            "PACKAGE", "SSM_CORPORATE_FORM", "FINANCIAL_STATEMENT", "BANK_STATEMENT",
            "IDENTITY_DOCUMENT", "CONSENT_FORM", "CUSTOMER_INFORMATION",
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


class TestPackageCompletenessGate:
    """FINDINGS #1: almost every rule degrades to not_applicable on a missing
    document, so without a gate outside the rule loop a near-empty package
    reads as a clean pass. package.completeness is that gate."""

    _EMPTY = {
        "bundle_id": "BUNDLE-EMPTY",
        "metadata": {
            "total_documents_received": 0,
            "system_date": "2026-07-08",
            "document_types_present": [],
        },
        "extracted_documents": [],
    }

    def test_empty_bundle_no_longer_reads_as_a_clean_pass(self):
        report = _run(self._EMPTY)
        gate = next(r for r in report.results if r.rule_id == "package.completeness")
        assert gate.passed is False
        assert gate.status is ValidationStatus.FAILED
        assert report.overall_passed is False

    def test_gate_always_runs_and_is_never_not_applicable(self):
        # The whole point: unlike every other rule, this one can't degrade to
        # not_applicable when documents are missing -- that's what it checks.
        report = _run(self._EMPTY)
        gate = next(r for r in report.results if r.rule_id == "package.completeness")
        assert gate.status is not ValidationStatus.NOT_APPLICABLE

    def test_complete_package_passes_the_gate(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        gate = next(r for r in report.results if r.rule_id == "package.completeness")
        assert gate.passed is True

    def test_gate_is_reported_under_its_own_document_group(self, passing_bundle_raw):
        report = _run(passing_bundle_raw)
        package_checks = {r.check for r in report.results_by_document["PACKAGE"]}
        assert package_checks == {"verify_required_documents_present"}

    def test_sole_prop_may_satisfy_the_financials_slot_with_a_tax_declaration(self):
        raw = dict(self._EMPTY)
        raw["extracted_documents"] = [
            {
                "document_id": "doc_ssm", "document_type": "ssm_corporate_form",
                "data": {
                    "entity_name": "SOLO TRADING", "business_registration_number": "SP001",
                    "entity_type": "Sole Proprietor",
                },
            },
            {
                "document_id": "doc_tax", "document_type": "tax_declaration",
                "data": {"entity_name": "SOLO TRADING", "financial_year_end": "2025-12-31"},
            },
        ]
        report = _run(raw)
        gate = next(r for r in report.results if r.rule_id == "package.completeness")
        missing = [slot for slot in gate.details["missing_document_slots"]]
        assert ["financial_statement", "tax_declaration"] not in missing

    def test_sdn_bhd_may_not_substitute_a_tax_declaration_for_audited_statements(self):
        raw = dict(self._EMPTY)
        raw["extracted_documents"] = [
            {
                "document_id": "doc_ssm", "document_type": "ssm_corporate_form",
                "data": {
                    "entity_name": "ALPHA SDN BHD", "business_registration_number": "202301",
                    "entity_type": "Sdn Bhd",
                },
            },
            {
                "document_id": "doc_tax", "document_type": "tax_declaration",
                "data": {"entity_name": "ALPHA SDN BHD", "financial_year_end": "2025-12-31"},
            },
        ]
        report = _run(raw)
        gate = next(r for r in report.results if r.rule_id == "package.completeness")
        assert ["financial_statement"] in gate.details["missing_document_slots"]


class TestPassportDirector:
    """TICKET-6: a director holding a passport instead of a Malaysian IC is
    validated against the passport requirements, not reported as missing an IC."""

    def _passport_bundle(self, **identity_overrides) -> dict:
        identity_data = {
            "individual_name": "JAMES WRIGHT",
            "nric_passport": "A12345678",
            "id_type": "passport",
            "front_image_present": True,
            "back_image_present": None,
            **identity_overrides,
        }
        return {
            "bundle_id": "BUNDLE-PASSPORT",
            "metadata": {
                "total_documents_received": 2,
                "system_date": "2026-07-08",
                "document_types_present": ["ssm_corporate_form", "identity_document"],
            },
            "extracted_documents": [
                {
                    "document_id": "doc_ssm",
                    "document_type": "ssm_corporate_form",
                    "data": {
                        "entity_name": "ALPHA TECH SOLUTIONS SDN BHD",
                        "business_registration_number": "202301098765",
                        "entity_type": "Sdn Bhd",
                        "directors": [{
                            "name": "JAMES WRIGHT",
                            "nric_passport": "A12345678",
                            "id_type": "passport",
                        }],
                    },
                },
                {
                    "document_id": "doc_passport",
                    "document_type": "identity_document",
                    "data": identity_data,
                },
            ],
        }

    def test_passport_holder_is_not_reported_as_missing_an_ic(self):
        report = _run(self._passport_bundle())
        coverage = next(r for r in report.results if r.check == "find_missing_ic_documents")
        assert coverage.passed is True
        assert coverage.details["missing_people"] == []

    def test_absent_back_image_does_not_fail_a_passport(self):
        report = _run(self._passport_bundle(back_image_present=False))
        sides = next(r for r in report.results if r.check == "check_ic_front_and_back")
        assert sides.passed is True

    def test_missing_passport_bio_data_page_still_fails(self):
        report = _run(self._passport_bundle(front_image_present=False))
        sides = next(r for r in report.results if r.check == "check_ic_front_and_back")
        assert sides.passed is False
        assert sides.status is ValidationStatus.FAILED

    def test_passport_number_is_still_matched_against_the_ssm_record(self):
        report = _run(self._passport_bundle(nric_passport="B99999999"))
        number_match = next(r for r in report.results if r.check.startswith("strict_match_ic_numbers["))
        assert number_match.passed is False
        assert number_match.status is ValidationStatus.FAILED


class TestUnreadableIcImageNeedsReview:
    """TICKET-5: an unreadable (null) IC side is 'needs review', never a pass.

    Verified, not fixed -- the tri-state was already handled correctly; these
    lock the behaviour in end-to-end so it can't silently collapse into a pass.
    """

    def _with_identity_flags(self, passing_bundle_raw, **flags) -> dict:
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], **flags))
            if doc["document_type"] == "identity_document"
            else doc
            for doc in raw["extracted_documents"]
        ]
        return raw

    def test_unreadable_front_is_needs_review_not_passed(self, passing_bundle_raw):
        report = _run(self._with_identity_flags(passing_bundle_raw, front_image_present=None))
        sides = next(r for r in report.results if r.check == "check_ic_front_and_back")
        assert sides.passed is None
        assert sides.status is ValidationStatus.NEEDS_REVIEW
        assert report.overall_status is ValidationStatus.NEEDS_REVIEW

    def test_unreadable_back_is_needs_review_not_passed(self, passing_bundle_raw):
        report = _run(self._with_identity_flags(passing_bundle_raw, back_image_present=None))
        sides = next(r for r in report.results if r.check == "check_ic_front_and_back")
        assert sides.status is ValidationStatus.NEEDS_REVIEW

    def test_confirmed_missing_side_still_outranks_unreadable(self, passing_bundle_raw):
        report = _run(self._with_identity_flags(
            passing_bundle_raw, front_image_present=None, back_image_present=False,
        ))
        sides = next(r for r in report.results if r.check == "check_ic_front_and_back")
        assert sides.status is ValidationStatus.FAILED


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

        # A skipped check never flips overall_passed to False on its own --
        # but the missing documents those checks skipped over are exactly what
        # package.completeness (FINDINGS #1) is there to fail on.
        assert all(
            r.status is ValidationStatus.NOT_APPLICABLE
            for r in report.results
            if r.rule_id != "package.completeness"
        )
        assert next(r for r in report.results if r.rule_id == "package.completeness").passed is False

    def test_empty_bundle_produces_only_skips_plus_a_failed_completeness_gate(self):
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
        assert all(
            r.passed is None for r in report.results if r.rule_id != "package.completeness"
        )
        assert report.entity_name == ""
        assert report.entity_type == ""
        # Nothing in the bundle to object to, rule by rule -- the gate is the
        # only thing standing between an empty package and a clean pass.
        assert report.overall_passed is False


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

    def test_entity_name_mismatch_does_not_suppress_other_checks(self, passing_bundle_raw):
        # TICKET-8: confirm a genuine entity_name mismatch fails its own check
        # and nothing else -- run_all_rules never short-circuits (registry.py
        # always runs every catalog rule regardless of prior outcomes), so an
        # unrelated rule must still report its own independent status rather
        # than being skipped or masked because another check failed.
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], entity_name="A COMPLETELY DIFFERENT ENTITY BHD"))
            if doc["document_type"] == "bank_statement"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        assert report.overall_passed is False

        unrelated_checks = {
            "calculate_financial_18_month_rule": ValidationStatus.PASSED,
            "check_bank_statement_freshness": ValidationStatus.PASSED,
            "verify_bank_statement_duration": ValidationStatus.PASSED,
            "verify_customer_information_completeness": ValidationStatus.PASSED,
        }
        for check_name, expected_status in unrelated_checks.items():
            result = next(r for r in report.results if r.check == check_name)
            assert result.status is expected_status, (
                f"{check_name} should be unaffected by the entity_name mismatch, "
                f"got {result.status}"
            )

    def test_changed_ic_number_is_caught_not_dropped(self, passing_bundle_raw):
        # Regression for the fail-open bug: a director's identity_document
        # NRIC no longer matching the SSM record must surface as a FAILED
        # strict_match_ic_numbers[...] result, not silently disappear from
        # the report (the join is by name now, not by the NRIC being compared).
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], nric_passport="990101-01-9999"))
            if doc["document_id"] == "doc_005"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        ic_number_checks = [r for r in report.results if r.check.startswith("strict_match_ic_numbers[")]
        assert len(ic_number_checks) == 2
        aiman_check = next(r for r in ic_number_checks if "MOHD AIMAN" in r.check)
        assert aiman_check.passed is False
        assert aiman_check.status is ValidationStatus.FAILED
        assert report.overall_passed is False

    def test_unmatched_identity_document_needs_review_not_silence(self, passing_bundle_raw):
        # If no identity document's name plausibly belongs to a director at
        # all, that director must still get a result (NEEDS_REVIEW), not be
        # skipped as if nothing needed comparing.
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], individual_name="ZAINAL BIN ABU BAKAR"))
            if doc["document_id"] == "doc_005"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        ic_number_checks = [r for r in report.results if r.check.startswith("strict_match_ic_numbers[")]
        assert len(ic_number_checks) == 2
        aiman_check = next(r for r in ic_number_checks if "MOHD AIMAN" in r.check)
        assert aiman_check.passed is None
        assert aiman_check.status is ValidationStatus.NEEDS_REVIEW
        # The other director's own document is untouched and still matches.
        ain_check = next(r for r in ic_number_checks if "NURUL AIN" in r.check)
        assert ain_check.passed is True
        assert report.overall_passed is True  # NEEDS_REVIEW never flips overall_passed

    def test_ocr_name_variant_still_matches_via_fuzzy_join(self, passing_bundle_raw):
        # A minor OCR-style name variant on the identity document (dropped
        # "BIN") should still join to the right director by name and then
        # correctly compare NRIC, rather than failing to join at all.
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], individual_name="MOHD AIMAN ZULKIFLI"))
            if doc["document_id"] == "doc_005"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        ic_number_checks = [r for r in report.results if r.check.startswith("strict_match_ic_numbers[")]
        aiman_check = next(r for r in ic_number_checks if "MOHD AIMAN" in r.check)
        assert aiman_check.passed is True
        assert report.overall_passed is True

    def test_shareholder_with_no_ic_is_silently_skipped_not_flagged(self, passing_bundle_raw):
        # Shareholders aren't required to submit an IC at all (unlike
        # directors) -- a shareholder with no identity document in the bundle
        # must produce no strict_match_ic_numbers result for them, not a
        # NEEDS_REVIEW "no confident match" warning. Regression caught by the
        # live-pipeline empirical check: the fix's greedy name-join initially
        # flagged every unmatched person, including shareholders who were
        # never expected to have a document at all.
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(
                doc["data"],
                shareholders=(doc["data"].get("shareholders") or []) + [
                    {"name": "TENGKU IDRIS BIN ISMAIL", "nric_passport": "700101-14-5555"}
                ],
            ))
            if doc["document_type"] == "ssm_corporate_form" and doc["data"].get("shareholders")
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        ic_number_checks = [r for r in report.results if r.check.startswith("strict_match_ic_numbers[")]
        assert not any("TENGKU IDRIS" in r.check for r in ic_number_checks)
        assert report.overall_passed is True


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

    def test_missing_customer_information_field_is_caught(self, passing_bundle_raw):
        raw = passing_bundle_raw.copy()
        raw["extracted_documents"] = [
            dict(doc, data=dict(doc["data"], company_office_status=""))
            if doc["document_type"] == "customer_information"
            else doc
            for doc in raw["extracted_documents"]
        ]
        report = _run(raw)
        check = next(r for r in report.results if r.check == "verify_customer_information_completeness")
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
