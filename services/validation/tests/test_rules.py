"""
Unit tests for services/validation/rules/*.py — each rule function in isolation,
with plain dicts as input (their actual calling contract; see rules/*.py
module docstrings). No pydantic bundle, no FastAPI, no network.
"""

from services.validation.rules import (
    calculate_financial_18_month_rule,
    check_bank_statement_bank_consistency,
    check_bank_statement_continuity,
    check_bank_statement_currency,
    check_bank_statement_freshness,
    check_bank_statement_overdraft,
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
    verify_customer_information_completeness,
    verify_bank_statement_duration,
    verify_consent_signatures,
    verify_financial_sections_present,
)


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

    def test_unconfirmed_section_is_needs_review_not_failed(self):
        # null ("couldn't determine") must NOT be treated the same as False
        # ("confirmed absent") -- this is the tri-state fix.
        result = verify_financial_sections_present(
            [{"entity_name": "X", "financial_year_end": "2025-12-31",
              "balance_sheet_present": True, "profit_and_loss_present": None,
              "cash_flow_present": True, "auditors_report_present": True}]
        )
        assert result["passed"] is None  # needs review, not a failure
        assert result["details"]["incomplete_documents"] == []
        assert result["details"]["needs_review_documents"][0]["unconfirmed_sections"] == ["Profit & Loss"]

    def test_confirmed_missing_outranks_unconfirmed(self):
        # A real confirmed-False failure must still fail the check even if
        # another section on the same document is merely unconfirmed.
        result = verify_financial_sections_present(
            [{"entity_name": "X", "financial_year_end": "2025-12-31",
              "balance_sheet_present": False, "profit_and_loss_present": None,
              "cash_flow_present": True, "auditors_report_present": True}]
        )
        assert result["passed"] is False


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

    def test_unconfirmed_side_is_needs_review_not_failed(self):
        result = check_ic_front_and_back(
            [{"individual_name": "A", "nric_passport": "1", "front_image_present": True, "back_image_present": None}]
        )
        assert result["passed"] is None
        assert result["details"]["incomplete_documents"] == []
        assert result["details"]["needs_review_documents"][0]["unconfirmed_sides"] == ["back"]

    def test_confirmed_missing_outranks_unconfirmed(self):
        result = check_ic_front_and_back(
            [{"individual_name": "A", "nric_passport": "1", "front_image_present": False, "back_image_present": None}]
        )
        assert result["passed"] is False


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

    def test_unconfirmed_signature_is_needs_review_not_failed(self):
        # null ("not confirmed either way") must NOT be treated the same as
        # False ("confirmed unsigned") -- this is the tri-state fix.
        ssm_people = [{"name": "A", "nric_passport": "1"}]
        consent_forms = [{"nric_passport": "1", "signature_present": None}]
        result = verify_consent_signatures(ssm_people, consent_forms)
        assert result["passed"] is None
        assert result["details"]["unsigned_consent"] == []
        assert len(result["details"]["unconfirmed_consent"]) == 1

    def test_missing_form_outranks_unconfirmed(self):
        ssm_people = [{"name": "A", "nric_passport": "1"}, {"name": "B", "nric_passport": "2"}]
        consent_forms = [{"nric_passport": "1", "signature_present": None}]  # B has no form at all
        result = verify_consent_signatures(ssm_people, consent_forms)
        assert result["passed"] is False


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


class TestCheckBankStatementFreshness:
    def test_recent_statement_passes(self):
        result = check_bank_statement_freshness("2026-06-30", "2026-07-07")
        assert result["passed"] is True

    def test_statement_within_two_months_passes(self):
        result = check_bank_statement_freshness("2026-05-10", "2026-07-09")
        assert result["passed"] is True

    def test_statement_older_than_two_months_fails(self):
        result = check_bank_statement_freshness("2026-01-01", "2026-07-07")
        assert result["passed"] is False


class TestCheckBankStatementOverdraft:
    def test_all_positive_balances_pass(self):
        result = check_bank_statement_overdraft([
            {"month": "January 2026", "end_balance": 1000.0},
            {"month": "February 2026", "end_balance": 500.5},
        ])
        assert result["passed"] is True

    def test_negative_balance_is_flagged(self):
        result = check_bank_statement_overdraft([
            {"month": "January 2026", "end_balance": 1000.0},
            {"month": "February 2026", "end_balance": -250.0},
        ])
        assert result["passed"] is False
        assert result["details"]["overdrawn_months"] == [{"month": "February 2026", "end_balance": -250.0}]

    def test_empty_list_passes_trivially(self):
        result = check_bank_statement_overdraft([])
        assert result["passed"] is True


class TestCheckBankStatementBankConsistency:
    def test_all_same_bank_passes(self):
        result = check_bank_statement_bank_consistency(["MAYBANK BERHAD", "MAYBANK BERHAD"])
        assert result["passed"] is True

    def test_mixed_banks_fails(self):
        result = check_bank_statement_bank_consistency(["MAYBANK BERHAD", "CIMB BANK BERHAD"])
        assert result["passed"] is False
        assert result["details"]["distinct_banks"] == ["CIMB BANK BERHAD", "MAYBANK BERHAD"]

    def test_any_unknown_bank_name_needs_review(self):
        result = check_bank_statement_bank_consistency(["MAYBANK BERHAD", None])
        assert result["passed"] is None
        assert result["details"]["documents_with_unknown_bank"] == 1

    def test_all_unknown_needs_review(self):
        result = check_bank_statement_bank_consistency([None, None])
        assert result["passed"] is None


class TestCheckBankStatementCurrency:
    def test_all_myr_passes(self):
        result = check_bank_statement_currency(["MYR", "MYR"], accepted_currency="MYR")
        assert result["passed"] is True

    def test_mismatched_currency_is_a_warning_not_a_fail(self):
        result = check_bank_statement_currency(["MYR", "SGD"], accepted_currency="MYR")
        assert result["passed"] is None
        assert result["details"]["mismatched_currencies"] == ["SGD"]

    def test_currency_comparison_is_case_and_whitespace_insensitive(self):
        result = check_bank_statement_currency([" myr ", "MYR"], accepted_currency="MYR")
        assert result["passed"] is True

    def test_unknown_currency_needs_review(self):
        result = check_bank_statement_currency(["MYR", None], accepted_currency="MYR")
        assert result["passed"] is None
        assert result["details"]["documents_with_unknown_currency"] == 1


def _full_customer_info():
    director = {
        "name": "AIMAN", "address": "ADDR", "email": "a@x.my", "religion": "Islam",
        "marital_status": "Married", "estimated_monthly_income": "15000",
        "experience_in_current_business": "10 years", "higher_education": "Degree",
        "emergency_contact_name": "ZUL", "emergency_contact_number": "+60123456781",
        "emergency_contact_relationship": "Father", "spouse_name": "SITI",
        "spouse_contact_number": "+60123456780",
    }
    return {
        "directors": [director],
        "company_age": "3 years", "company_number_of_staff": "12",
        "company_current_office_address": "OFFICE", "company_office_status": "Rented",
        "company_office_monthly_rent": "4500", "company_office_telephone": "+60341234567",
        "company_email_address": "info@x.my", "company_auditor_firm_name": "AZMAN & CO",
        "company_auditor_contact_person": "AZMAN", "company_auditor_contact_number": "+60341239999",
    }


class TestVerifyCustomerInformationCompleteness:
    def test_all_fields_present_passes(self):
        result = verify_customer_information_completeness(_full_customer_info())
        assert result["passed"] is True

    def test_missing_company_field_fails(self):
        data = _full_customer_info()
        data["company_office_status"] = ""
        result = verify_customer_information_completeness(data)
        assert result["passed"] is False
        assert "Company Office Status" in result["details"]["missing_fields"]

    def test_missing_director_field_fails(self):
        data = _full_customer_info()
        data["directors"][0]["spouse_name"] = ""
        result = verify_customer_information_completeness(data)
        assert result["passed"] is False
        assert "Director[0] Director Spouse Name" in result["details"]["missing_fields"]

    def test_no_directors_fails(self):
        data = _full_customer_info()
        data["directors"] = []
        result = verify_customer_information_completeness(data)
        assert result["passed"] is False

    def test_all_fields_missing_fails(self):
        result = verify_customer_information_completeness({})
        assert result["passed"] is False
        # 10 company fields + the "no directors" entry
        assert len(result["details"]["missing_fields"]) == 11
