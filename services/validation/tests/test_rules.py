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
    match_people_by_name,
    months_between,
    person_similarity,
    strict_match_entity_names,
    strict_match_ic_numbers,
    verify_customer_information_completeness,
    verify_bank_statement_duration,
    verify_consent_signatures,
    verify_financial_sections_present,
    verify_required_documents_present,
)
from services.validation.rules._utils import NOT_AVAILABLE, normalize_id_type
from services.validation.domain.policies import ValidationPolicy


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
        assert result["details"]["missing_people"] == [
            {"name": "A", "nric_passport": "880214-14-5123", "id_type": None}
        ]

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


class TestNormalizeIdType:
    def test_mykad_spellings_resolve_to_mykad(self):
        for raw in ("MyKad", "mykad", "MY KAD", "IC", "NRIC", "Identity Card", "Kad Pengenalan"):
            assert normalize_id_type(raw) == "mykad", raw

    def test_passport_spellings_resolve_to_passport(self):
        for raw in ("Passport", "PASSPORT", "pasport", "International Passport"):
            assert normalize_id_type(raw) == "passport", raw

    def test_blank_or_unrecognized_stays_unknown(self):
        for raw in (None, "", "   ", "Driving Licence"):
            assert normalize_id_type(raw) is None, raw


class TestPassportIdentityDocuments:
    """TICKET-6: a passport is a recognized alternate identity document.

    A passport has no IC-style back: one bio-data page image is the whole
    requirement, so a passport-holding director must not be failed for a
    "missing back" or reported as missing an IC.
    """

    def test_passport_needs_only_a_bio_data_page(self):
        result = check_ic_front_and_back([{
            "individual_name": "A", "nric_passport": "A12345678", "id_type": "passport",
            "front_image_present": True, "back_image_present": False,
        }])
        assert result["passed"] is True

    def test_passport_without_a_bio_data_page_fails(self):
        result = check_ic_front_and_back([{
            "individual_name": "A", "nric_passport": "A12345678", "id_type": "passport",
            "front_image_present": False, "back_image_present": None,
        }])
        assert result["passed"] is False
        assert result["details"]["incomplete_documents"][0]["missing_sides"] == ["bio_data_page"]

    def test_unreadable_passport_bio_data_page_is_needs_review(self):
        result = check_ic_front_and_back([{
            "individual_name": "A", "nric_passport": "A12345678", "id_type": "passport",
            "front_image_present": None, "back_image_present": None,
        }])
        assert result["passed"] is None
        assert result["details"]["needs_review_documents"][0]["unconfirmed_sides"] == ["bio_data_page"]

    def test_unknown_id_type_is_held_to_the_stricter_mykad_requirement(self):
        # An unrecognized/absent ID Type must not weaken the check -- MyKad is
        # both the common case and the stricter of the two requirements.
        result = check_ic_front_and_back([{
            "individual_name": "A", "nric_passport": "880214-14-5123",
            "front_image_present": True, "back_image_present": False,
        }])
        assert result["passed"] is False
        assert result["details"]["incomplete_documents"][0]["missing_sides"] == ["back"]

    def test_passport_holder_with_a_passport_is_not_missing_an_ic(self):
        ssm_people = [{"name": "A", "nric_passport": "A12345678", "id_type": "passport"}]
        ic_documents = [{"individual_name": "A", "nric_passport": "A12345678", "id_type": "passport"}]
        result = find_missing_ic_documents(ssm_people, ic_documents)
        assert result["passed"] is True

    def test_missing_passport_is_reported_as_a_passport_not_an_ic(self):
        result = find_missing_ic_documents(
            [{"name": "A", "nric_passport": "A12345678", "id_type": "Passport"}], [],
        )
        assert result["passed"] is False
        assert result["details"]["missing_people"][0]["id_type"] == "passport"
        assert "passport" in result["message"].lower()
        assert "IC" not in result["message"]


class TestVerifyRequiredDocumentsPresent:
    """FINDINGS #1: a near-empty package must not read as a clean pass."""

    _POLICY = ValidationPolicy(
        policy_id="test-slots",
        minimum_bank_statement_months_by_entity={"sole prop": 12},
        default_minimum_bank_statement_months=6,
        entity_type_aliases={"enterprise": "sole prop"},
        required_document_slots=[
            ["ssm_corporate_form"], ["financial_statement"], ["bank_statement"],
        ],
        required_document_slots_by_entity={
            "sole prop": [
                ["ssm_corporate_form"],
                ["financial_statement", "tax_declaration"],
                ["bank_statement"],
            ],
        },
    )

    def _check(self, present, entity_type="Sdn Bhd"):
        return verify_required_documents_present(present, entity_type, policy=self._POLICY)

    def test_empty_package_fails_instead_of_passing_vacuously(self):
        result = self._check([])
        assert result["passed"] is False
        assert result["details"]["missing_document_slots"] == self._POLICY.required_document_slots

    def test_complete_package_passes(self):
        result = self._check(["ssm_corporate_form", "financial_statement", "bank_statement"])
        assert result["passed"] is True
        assert result["details"]["missing_document_slots"] == []

    def test_partially_filled_package_names_only_what_is_missing(self):
        result = self._check(["ssm_corporate_form"])
        assert result["passed"] is False
        assert result["details"]["missing_document_slots"] == [
            ["financial_statement"], ["bank_statement"],
        ]
        assert "ssm_corporate_form" not in result["message"]

    def test_document_types_beyond_the_required_slots_are_not_flagged(self):
        result = self._check(
            ["ssm_corporate_form", "financial_statement", "bank_statement", "consent_form"],
        )
        assert result["passed"] is True
        assert "consent_form" in result["details"]["present_document_types"]

    def test_sole_prop_may_satisfy_the_financials_slot_with_a_tax_declaration(self):
        present = ["ssm_corporate_form", "tax_declaration", "bank_statement"]
        assert self._check(present, entity_type="Sole Prop")["passed"] is True
        assert "financial_statement or tax_declaration" in self._check(
            ["ssm_corporate_form"], entity_type="Sole Prop",
        )["message"]

    def test_sdn_bhd_may_not_substitute_a_tax_declaration(self):
        result = self._check(["ssm_corporate_form", "tax_declaration", "bank_statement"])
        assert result["passed"] is False
        assert result["details"]["missing_document_slots"] == [["financial_statement"]]

    def test_entity_type_resolves_through_the_alias_table(self):
        # "Enterprise" is a sole prop, so it gets the Borang B alternative --
        # the same resolution the bank-statement minimum uses.
        result = self._check(
            ["ssm_corporate_form", "tax_declaration", "bank_statement"], entity_type="Enterprise",
        )
        assert result["details"]["resolved_entity_type_key"] == "sole prop"
        assert result["passed"] is True

    def test_unresolvable_entity_type_falls_back_to_the_default_slots(self):
        result = self._check(["ssm_corporate_form", "bank_statement"], entity_type="")
        assert result["details"]["resolved_entity_type_key"] is None
        assert result["details"]["missing_document_slots"] == [["financial_statement"]]


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

    def test_single_consolidated_document_with_covered_months_gap_fails(self):
        # One BankStatementDoc (e.g. from extraction_adapter's from-extraction
        # path) spanning Jan-Jun but with no transactions in March -- the
        # start/end date range alone can't reveal this, only covered_months.
        result = check_bank_statement_continuity(
            [{
                "start_date": "2026-01-01", "end_date": "2026-06-30",
                "covered_months": ["2026-01", "2026-02", "2026-04", "2026-05", "2026-06"],
            }]
        )
        assert result["passed"] is False
        assert result["details"]["issues"][0]["type"] == "gap"
        assert "2026-03" in result["details"]["issues"][0]["missing_months"]

    def test_single_consolidated_document_with_full_covered_months_passes(self):
        result = check_bank_statement_continuity(
            [{
                "start_date": "2026-01-01", "end_date": "2026-06-30",
                "covered_months": ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"],
            }]
        )
        assert result["passed"] is True

    def test_two_documents_sharing_a_month_via_covered_months_is_an_overlap(self):
        result = check_bank_statement_continuity(
            [{"start_date": "2026-01-01", "end_date": "2026-03-31", "covered_months": ["2026-01", "2026-02", "2026-03"]},
             {"start_date": "2026-03-01", "end_date": "2026-04-30", "covered_months": ["2026-03", "2026-04"]}]
        )
        assert result["passed"] is False
        assert result["details"]["issues"][0]["type"] == "overlap"
        assert "2026-03" in result["details"]["issues"][0]["overlapping_months"]


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

    def test_day_of_month_alignment_no_longer_causes_an_off_by_one_undercount(self):
        # Regression for the bug where relativedelta(2026-06-30, 2026-01-31)
        # has a zero day-remainder, so the old "+1 only if rd.days > 0" logic
        # undercounted this ordinary 6-month span as 5.
        statements = [{"start_date": "2026-01-31", "end_date": "2026-06-30"}]
        result = verify_bank_statement_duration(statements, "Sdn Bhd")
        assert result["details"]["months_covered"] == 6
        assert result["passed"] is True

    def test_months_covered_uses_covered_months_when_present(self):
        # A single consolidated document missing March: months_covered must
        # reflect the true 5 distinct months, and continuity must catch the
        # gap rather than duration silently computing 6 from the date range.
        statements = [{
            "start_date": "2026-01-01", "end_date": "2026-06-30",
            "covered_months": ["2026-01", "2026-02", "2026-04", "2026-05", "2026-06"],
        }]
        result = verify_bank_statement_duration(statements, "Sdn Bhd")
        assert result["passed"] is False
        assert "not continuous" in result["message"]

    def test_malay_sole_prop_term_resolves_to_12_months(self):
        # TICKET-7: "Perniagaan Tunggal" (Malay for sole proprietorship) must
        # resolve to the 12-month sole-prop requirement via the alias table,
        # not silently fall through to the lenient 6-month default.
        statements = [{"start_date": "2025-01-01", "end_date": "2025-06-30"}]
        result = verify_bank_statement_duration(statements, "Perniagaan Tunggal")
        assert result["details"]["resolved_entity_type_key"] == "sole prop"
        assert result["details"]["minimum_required_months"] == 12
        assert result["passed"] is False  # only 6 months on file

    def test_enterprise_suffix_resolves_to_sole_prop(self):
        statements = [{"start_date": "2024-07-01", "end_date": "2025-06-30"}]
        result = verify_bank_statement_duration(statements, "Enterprise")
        assert result["details"]["resolved_entity_type_key"] == "sole prop"
        assert result["details"]["minimum_required_months"] == 12
        assert result["passed"] is True  # 12 months on file

    def test_llp_resolves_to_partnership(self):
        statements = [{"start_date": "2025-01-01", "end_date": "2025-06-30"}]
        result = verify_bank_statement_duration(statements, "LLP")
        assert result["details"]["resolved_entity_type_key"] == "partnership"
        assert result["details"]["minimum_required_months"] == 12

    def test_typo_variant_falls_back_to_fuzzy_match(self):
        # Not a literal alias-table entry -- close enough to "sole
        # proprietor" to resolve via the conservative fuzzy fallback.
        statements = [{"start_date": "2025-01-01", "end_date": "2025-06-30"}]
        result = verify_bank_statement_duration(statements, "Sole Propietor")
        assert result["details"]["resolved_entity_type_key"] == "sole proprietor"
        assert result["details"]["minimum_required_months"] == 12

    def test_sdn_bhd_does_not_resolve_to_any_alias(self):
        # Sanity check: Sdn Bhd must not be swept up by the fuzzy fallback --
        # it stays on the (correct, for it) unresolved/default path.
        statements = [{"start_date": "2026-01-01", "end_date": "2026-06-30"}]
        result = verify_bank_statement_duration(statements, "Sdn Bhd")
        assert result["details"]["resolved_entity_type_key"] is None
        assert result["details"]["minimum_required_months"] == 6

    def test_unrecognized_entity_type_still_falls_through_to_default(self):
        # Genuinely unrecognized/blank strings are an explicitly deferred,
        # separate problem (TICKET-9) -- this ticket only extends the alias
        # table, it doesn't change the fallback's existence.
        statements = [{"start_date": "2026-01-01", "end_date": "2026-06-30"}]
        result = verify_bank_statement_duration(statements, "")
        assert result["details"]["resolved_entity_type_key"] is None
        assert result["details"]["minimum_required_months"] == 6


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


class TestMatchPeopleByName:
    def test_exact_names_pair_up_correctly(self):
        assignment = match_people_by_name(
            ssm_people=[("nric_a", "AHMAD BIN ALI"), ("nric_b", "SITI BINTI HASSAN")],
            candidates=[("doc_x", "AHMAD BIN ALI"), ("doc_y", "SITI BINTI HASSAN")],
        )
        assert assignment == {"nric_a": "doc_x", "nric_b": "doc_y"}

    def test_no_candidate_above_threshold_maps_to_none(self):
        assignment = match_people_by_name(
            ssm_people=[("nric_a", "AHMAD BIN ALI")],
            candidates=[("doc_x", "A COMPLETELY UNRELATED NAME")],
        )
        assert assignment == {"nric_a": None}

    def test_no_candidates_at_all_maps_every_person_to_none(self):
        assignment = match_people_by_name(
            ssm_people=[("nric_a", "AHMAD BIN ALI"), ("nric_b", "SITI BINTI HASSAN")],
            candidates=[],
        )
        assert assignment == {"nric_a": None, "nric_b": None}

    def test_greedy_assignment_does_not_double_claim_a_candidate(self):
        # Two people with the identical declared name and two candidates with
        # that same name: both people score 1.0 against both candidates, so a
        # naive "first match wins" join could hand both people the same
        # candidate. Assignment must be 1:1.
        assignment = match_people_by_name(
            ssm_people=[("nric_a", "AHMAD BIN ALI"), ("nric_b", "AHMAD BIN ALI")],
            candidates=[("doc_x", "AHMAD BIN ALI"), ("doc_y", "AHMAD BIN ALI")],
        )
        assert set(assignment.values()) == {"doc_x", "doc_y"}
        assert None not in assignment.values()


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
        assert result["details"]["distinct_banks"] == ["CIMB BANK", "MAYBANK"]

    def test_any_unknown_bank_name_needs_review(self):
        result = check_bank_statement_bank_consistency(["MAYBANK BERHAD", None])
        assert result["passed"] is None
        assert result["details"]["documents_with_unknown_bank"] == 1

    def test_all_unknown_needs_review(self):
        result = check_bank_statement_bank_consistency([None, None])
        assert result["passed"] is None

    def test_legal_name_variants_recognised_as_one_bank(self):
        # TICKET-3: "Maybank", "Maybank Berhad", and "Malayan Banking Berhad"
        # must all resolve to the same bank via the alias table.
        result = check_bank_statement_bank_consistency(
            ["Maybank", "Maybank Berhad", "Malayan Banking Berhad"]
        )
        assert result["passed"] is True
        assert result["details"]["distinct_banks"] == ["MAYBANK"]
        assert result["details"]["raw_bank_names"] == [
            "Malayan Banking Berhad", "Maybank", "Maybank Berhad",
        ]

    def test_unseen_typo_variant_falls_back_to_fuzzy_match(self):
        # Not a literal alias-table entry (nor resolvable via legal-suffix
        # stripping alone) -- a one-off OCR typo close enough to "Maybank" to
        # resolve via the conservative fuzzy fallback rather than being read
        # as a different bank.
        result = check_bank_statement_bank_consistency(["Mayabnk Berhad", "Maybank Berhad"])
        assert result["passed"] is True
        assert result["details"]["distinct_banks"] == ["MAYBANK"]

    def test_genuinely_different_banks_are_not_conflated_by_fuzzy_match(self):
        result = check_bank_statement_bank_consistency(["Public Bank Berhad", "Hong Leong Bank Berhad"])
        assert result["passed"] is False
        assert result["details"]["distinct_banks"] == ["HONG LEONG BANK", "PUBLIC BANK"]


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

    def test_not_available_sentinel_still_flagged_as_missing(self):
        # TICKET-2: the adapter now substitutes "Not Available" instead of ""
        # for a missing field -- the completeness rule must still flag it,
        # not read it as a genuinely-filled value.
        data = _full_customer_info()
        data["company_office_status"] = NOT_AVAILABLE
        data["directors"][0]["spouse_name"] = NOT_AVAILABLE
        result = verify_customer_information_completeness(data)
        assert result["passed"] is False
        assert "Company Office Status" in result["details"]["missing_fields"]
        assert "Director[0] Director Spouse Name" in result["details"]["missing_fields"]

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
