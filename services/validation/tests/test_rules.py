"""
Unit tests for services/validation/rules/*.py — each rule function in isolation,
with plain dicts as input (their actual calling contract; see rules/*.py
module docstrings). No pydantic bundle, no FastAPI, no network.
"""

from services.validation.rules import (
    calculate_financial_18_month_rule,
    check_bank_statement_continuity,
    check_financial_consecutive_years,
    check_ic_front_and_back,
    entity_similarity,
    find_missing_ic_documents,
    fuzzy_match_entity_names,
    fuzzy_match_person_names,
    months_between,
    person_similarity,
    strict_match_entity_names,
    strict_match_ic_numbers,
    validate_form_d_expiry,
    verify_bank_statement_duration,
    verify_consent_signatures,
    verify_financial_sections_present,
    verify_ssm_completeness,
)


class TestVerifySsmCompleteness:
    def test_sdn_bhd_all_forms_present(self):
        result = verify_ssm_completeness("Sdn Bhd", ["form_24", "form_44", "form_49"])
        assert result["passed"] is True

    def test_sdn_bhd_missing_form(self):
        result = verify_ssm_completeness("Sdn Bhd", ["form_24", "form_44"])
        assert result["passed"] is False
        assert result["details"]["missing_forms"] == ["form_49"]

    def test_sole_proprietor_uses_form_b_and_d(self):
        result = verify_ssm_completeness("Sole Proprietor", ["form_b", "form_d"])
        assert result["passed"] is True

    def test_entity_type_matching_is_case_insensitive(self):
        result = verify_ssm_completeness("  sdn bhd  ", ["form_24", "form_44", "form_49"])
        assert result["passed"] is True


class TestVerifyFinancialSectionsPresent:
    def test_all_sections_present(self):
        result = verify_financial_sections_present(
            [{"entity_name": "X", "financial_year_end": "2025-12-31",
              "balance_sheet_present": True, "profit_and_loss_present": True,
              "cash_flow_present": True, "auditors_report_present": True}]
        )
        assert result["passed"] is True

    def test_missing_section_flagged(self):
        result = verify_financial_sections_present(
            [{"entity_name": "X", "financial_year_end": "2025-12-31",
              "balance_sheet_present": True, "profit_and_loss_present": False,
              "cash_flow_present": True, "auditors_report_present": True}]
        )
        assert result["passed"] is False
        assert result["details"]["incomplete_documents"][0]["missing_sections"] == ["Profit & Loss"]


class TestFindMissingIcDocuments:
    def test_everyone_has_ic(self):
        ssm_people = [{"name": "A", "nric_passport": "880214-14-5123"}]
        ic_docs = [{"nric_passport": "880214-14-5123"}]
        result = find_missing_ic_documents(ssm_people, ic_docs)
        assert result["passed"] is True

    def test_missing_ic_is_flagged(self):
        ssm_people = [{"name": "A", "nric_passport": "880214-14-5123"}]
        result = find_missing_ic_documents(ssm_people, [])
        assert result["passed"] is False
        assert result["details"]["missing_people"] == [{"name": "A", "nric_passport": "880214-14-5123"}]

    def test_nric_matching_ignores_formatting(self):
        ssm_people = [{"name": "A", "nric_passport": "880214-14-5123"}]
        ic_docs = [{"nric_passport": "880214145123"}]  # dashes stripped, same id
        result = find_missing_ic_documents(ssm_people, ic_docs)
        assert result["passed"] is True


class TestCheckIcFrontAndBack:
    def test_both_sides_present(self):
        result = check_ic_front_and_back([{"front_image_present": True, "back_image_present": True}])
        assert result["passed"] is True

    def test_back_missing(self):
        result = check_ic_front_and_back(
            [{"individual_name": "A", "nric_passport": "1", "front_image_present": True, "back_image_present": False}]
        )
        assert result["passed"] is False
        assert result["details"]["incomplete_documents"][0]["missing_sides"] == ["back"]


class TestVerifyConsentSignatures:
    def test_signed_consent_for_everyone(self):
        ssm_people = [{"name": "A", "nric_passport": "1"}]
        consent_forms = [{"nric_passport": "1", "signature_present": True}]
        result = verify_consent_signatures(ssm_people, consent_forms)
        assert result["passed"] is True

    def test_missing_consent_form(self):
        ssm_people = [{"name": "A", "nric_passport": "1"}]
        result = verify_consent_signatures(ssm_people, [])
        assert result["passed"] is False
        assert len(result["details"]["missing_consent"]) == 1
        assert result["details"]["unsigned_consent"] == []

    def test_unsigned_consent_form(self):
        ssm_people = [{"name": "A", "nric_passport": "1"}]
        consent_forms = [{"nric_passport": "1", "signature_present": False}]
        result = verify_consent_signatures(ssm_people, consent_forms)
        assert result["passed"] is False
        assert len(result["details"]["unsigned_consent"]) == 1


class TestCalculateFinancial18MonthRule:
    def test_within_limit(self):
        result = calculate_financial_18_month_rule("2025-01-01", "2026-01-01")
        assert result["passed"] is True

    def test_exceeds_limit(self):
        result = calculate_financial_18_month_rule("2024-01-01", "2026-06-01")
        assert result["passed"] is False

    def test_exactly_18_months_passes(self):
        result = calculate_financial_18_month_rule("2024-01-01", "2025-07-01")
        assert result["passed"] is True
        assert result["details"]["months_elapsed"] == 18

    def test_future_fye_fails(self):
        result = calculate_financial_18_month_rule("2027-01-01", "2026-01-01")
        assert result["passed"] is False


class TestCheckFinancialConsecutiveYears:
    def test_two_consecutive_years_pass(self):
        result = check_financial_consecutive_years(["2024-12-31", "2025-12-31"])
        assert result["passed"] is True

    def test_gap_year_fails(self):
        result = check_financial_consecutive_years(["2023-12-31", "2025-12-31"])
        assert result["passed"] is False

    def test_duplicate_year_fails(self):
        result = check_financial_consecutive_years(["2025-12-31", "2025-12-31"])
        assert result["passed"] is False

    def test_wrong_count_fails(self):
        result = check_financial_consecutive_years(["2025-12-31"])
        assert result["passed"] is False


class TestCheckBankStatementContinuity:
    def test_continuous_statements_pass(self):
        result = check_bank_statement_continuity(
            [{"start_date": "2026-01-01", "end_date": "2026-01-31"},
             {"start_date": "2026-02-01", "end_date": "2026-02-28"}]
        )
        assert result["passed"] is True

    def test_gap_between_statements_fails(self):
        result = check_bank_statement_continuity(
            [{"start_date": "2026-01-01", "end_date": "2026-01-31"},
             {"start_date": "2026-03-01", "end_date": "2026-03-31"}]
        )
        assert result["passed"] is False
        assert result["details"]["issues"][0]["type"] == "gap"

    def test_overlapping_statements_fails(self):
        result = check_bank_statement_continuity(
            [{"start_date": "2026-01-01", "end_date": "2026-01-31"},
             {"start_date": "2026-01-15", "end_date": "2026-02-28"}]
        )
        assert result["passed"] is False
        assert result["details"]["issues"][0]["type"] == "overlap"

    def test_unsorted_input_is_sorted_before_checking(self):
        result = check_bank_statement_continuity(
            [{"start_date": "2026-02-01", "end_date": "2026-02-28"},
             {"start_date": "2026-01-01", "end_date": "2026-01-31"}]
        )
        assert result["passed"] is True


class TestVerifyBankStatementDuration:
    def test_sdn_bhd_needs_6_months(self):
        statements = [{"start_date": "2026-01-01", "end_date": "2026-06-30"}]
        result = verify_bank_statement_duration(statements, "Sdn Bhd")
        assert result["passed"] is True

    def test_sdn_bhd_below_6_months_fails(self):
        statements = [{"start_date": "2026-01-01", "end_date": "2026-03-31"}]
        result = verify_bank_statement_duration(statements, "Sdn Bhd")
        assert result["passed"] is False

    def test_sole_proprietor_needs_12_months(self):
        statements = [{"start_date": "2025-01-01", "end_date": "2025-06-30"}]
        result = verify_bank_statement_duration(statements, "Sole Proprietor")
        assert result["passed"] is False

    def test_discontinuous_statements_fail_before_duration_is_checked(self):
        statements = [
            {"start_date": "2026-01-01", "end_date": "2026-01-31"},
            {"start_date": "2026-06-01", "end_date": "2026-06-30"},
        ]
        result = verify_bank_statement_duration(statements, "Sdn Bhd")
        assert result["passed"] is False
        assert "not continuous" in result["message"]


class TestValidateFormDExpiry:
    def test_expiry_covers_tenure(self):
        result = validate_form_d_expiry("2028-01-01", 12, "2026-01-01")
        assert result["passed"] is True

    def test_expiry_short_of_tenure_fails(self):
        result = validate_form_d_expiry("2026-06-01", 12, "2026-01-01")
        assert result["passed"] is False
        assert result["details"]["shortfall_days"] > 0


class TestMonthsBetween:
    def test_computes_whole_months_and_extra_days(self):
        result = months_between("2026-01-01", "2026-03-15")
        assert result["details"]["months"] == 2
        assert result["details"]["extra_days"] == 14


class TestEntityAndPersonMatching:
    def test_strict_match_entity_names_exact(self):
        result = strict_match_entity_names("ALPHA TECH SDN BHD", "ALPHA TECH SDN BHD")
        assert result["passed"] is True

    def test_strict_match_entity_names_punctuation_mismatch_fails(self):
        result = strict_match_entity_names("ALPHA TECH SDN BHD", "ALPHA TECH SDN. BHD.")
        assert result["passed"] is False

    def test_fuzzy_match_entity_names_tolerates_punctuation(self):
        result = fuzzy_match_entity_names("ALPHA TECH SDN BHD", "ALPHA TECH SDN. BHD.")
        assert result["passed"] is True

    def test_fuzzy_match_entity_names_rejects_different_entity(self):
        result = fuzzy_match_entity_names("ALPHA TECH SDN BHD", "BETA HOLDINGS SDN BHD")
        assert result["passed"] is False

    def test_strict_match_ic_numbers_ignores_dashes(self):
        result = strict_match_ic_numbers("880214-14-5123", "880214145123")
        assert result["passed"] is True

    def test_strict_match_ic_numbers_real_mismatch_fails(self):
        result = strict_match_ic_numbers("880214-14-5123", "880214-14-5124")
        assert result["passed"] is False

    def test_fuzzy_match_person_names_tolerates_malay_spelling_variants(self):
        result = fuzzy_match_person_names("MOHD AIMAN BIN ZULKIFLI", "MUHAMMAD AIMAN BIN ZULKIFLI")
        assert result["passed"] is True

    def test_fuzzy_match_person_names_rejects_different_person(self):
        result = fuzzy_match_person_names("MOHD AIMAN BIN ZULKIFLI", "NURUL AIN BINTI ZULKIFLI")
        assert result["passed"] is False

    def test_entity_similarity_is_1_for_identical_names(self):
        assert entity_similarity("ALPHA TECH SDN BHD", "ALPHA TECH SDN BHD") == 1.0

    def test_person_similarity_is_1_for_aliased_names(self):
        assert person_similarity("MOHD AIMAN", "MUHAMMAD AIMAN") == 1.0
