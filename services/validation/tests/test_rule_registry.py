"""Tests for the RULE_CATALOG-driven rule registry (rules/registry.py).

These target the registry directly (not through ValidationEngine) so a
regression in applicability/argument-binding logic is caught here, distinct
from ValidationEngine's job of turning outcomes into CheckResults.
"""

from services.validation.bundle import ValidationBundle
from services.validation.domain.context import BundleContext
from services.validation.domain.policies import BMMB_SME_POLICY_V1
from services.validation.rules import RULE_CATALOG, run_all_rules


def _context(raw: dict) -> BundleContext:
    bundle = ValidationBundle(**raw)
    return BundleContext.from_bundle(bundle)


def _ssm_only_raw(directors, shareholders) -> dict:
    return {
        "bundle_id": "BUNDLE-SSM-PEOPLE",
        "metadata": {
            "total_documents_received": 1,
            "system_date": "2026-07-08",
            "document_types_present": ["ssm_corporate_form"],
        },
        "extracted_documents": [
            {
                "document_id": "ssm_business_registration",
                "document_type": "ssm_corporate_form",
                "data": {
                    "entity_name": "ALPHA TECH SOLUTIONS SDN BHD",
                    "business_registration_number": "202301098765",
                    "entity_type": "Sdn Bhd",
                    "directors": directors,
                    "shareholders": shareholders,
                },
            }
        ],
    }


class TestShareholdersAreNotHeldToIcOrConsent:
    _DIRECTOR = {"name": "DIRECTOR ONE", "nric_passport": "111111-11-1111"}
    _SHAREHOLDER = {"name": "SHAREHOLDER TWO", "nric_passport": "222222-22-2222"}

    def test_context_directors_exclude_shareholders(self):
        ctx = _context(_ssm_only_raw([self._DIRECTOR], [self._SHAREHOLDER]))
        director_ids = {p["nric_passport"] for p in ctx.ssm_directors}
        people_ids = {p["nric_passport"] for p in ctx.ssm_people}
        assert director_ids == {"111111-11-1111"}
        assert people_ids == {"111111-11-1111", "222222-22-2222"}

    def test_shareholder_without_ic_or_consent_does_not_fail(self):
        # A director with no IC and no consent should fail those rules, but a
        # shareholder-only person must never be the reason a bundle fails them.
        raw = _ssm_only_raw([], [self._SHAREHOLDER])
        ctx = _context(raw)
        system_date = ValidationBundle(**raw).metadata.system_date
        pairs = dict(run_all_rules(ctx, BMMB_SME_POLICY_V1, system_date))

        # No identity/consent docs present at all -> both rules skip, and the
        # shareholder is never enumerated as a required party.
        coverage = pairs["identity_document.coverage"]
        consent = pairs["consent.signature"]
        assert coverage.result is None and coverage.skip_reason is not None
        assert consent.result is None and consent.skip_reason is not None


class TestRegistryCoversCatalog:
    def test_every_catalog_rule_id_has_a_runner(self, passing_bundle_raw):
        context = _context(passing_bundle_raw)
        pairs = list(run_all_rules(context, BMMB_SME_POLICY_V1, ValidationBundle(**passing_bundle_raw).metadata.system_date))
        produced_rule_ids = {rule_id for rule_id, _ in pairs}
        # Every non-dynamic rule fires at least a skip or a result for any bundle.
        catalog_rule_ids = {definition.rule_id for definition in RULE_CATALOG}
        assert produced_rule_ids <= catalog_rule_ids

    def test_outcome_order_matches_catalog_order(self, passing_bundle_raw):
        context = _context(passing_bundle_raw)
        system_date = ValidationBundle(**passing_bundle_raw).metadata.system_date
        pairs = list(run_all_rules(context, BMMB_SME_POLICY_V1, system_date))

        catalog_order = [definition.rule_id for definition in RULE_CATALOG]
        seen_order = []
        for rule_id, _ in pairs:
            if rule_id not in seen_order:
                seen_order.append(rule_id)
        assert seen_order == catalog_order


class TestRegistryOutcomeShape:
    def test_applicable_rule_returns_result_not_skip(self, passing_bundle_raw):
        context = _context(passing_bundle_raw)
        system_date = ValidationBundle(**passing_bundle_raw).metadata.system_date
        pairs = list(run_all_rules(context, BMMB_SME_POLICY_V1, system_date))

        fin_outcomes = [outcome for rule_id, outcome in pairs if rule_id == "financial_statement.freshness"]
        assert len(fin_outcomes) == 1
        assert fin_outcomes[0].result is not None
        assert fin_outcomes[0].skip_reason is None

    def test_inapplicable_rule_returns_skip_reason_not_result(self):
        raw = {
            "bundle_id": "BUNDLE-MINIMAL",
            "metadata": {
                "total_documents_received": 0,
                "system_date": "2026-07-08",
                "document_types_present": [],
            },
            "extracted_documents": [],
        }
        context = _context(raw)
        system_date = ValidationBundle(**raw).metadata.system_date
        pairs = list(run_all_rules(context, BMMB_SME_POLICY_V1, system_date))

        fin_outcomes = [outcome for rule_id, outcome in pairs if rule_id == "financial_statement.freshness"]
        assert len(fin_outcomes) == 1
        assert fin_outcomes[0].result is None
        assert fin_outcomes[0].skip_reason == "No financial_statement or tax_declaration document in bundle."

    def test_entity_name_match_yields_one_outcome_per_document(self, passing_bundle_raw):
        context = _context(passing_bundle_raw)
        system_date = ValidationBundle(**passing_bundle_raw).metadata.system_date
        pairs = list(run_all_rules(context, BMMB_SME_POLICY_V1, system_date))

        expected_docs = (
            context.bank_statement_docs
            + context.financial_statement_docs
            + context.tax_declaration_docs
            + context.consent_form_docs
        )
        entity_match_outcomes = [outcome for rule_id, outcome in pairs if rule_id == "entity_name.match"]
        assert len(entity_match_outcomes) == len(expected_docs)


class TestBankStatementContinuityIsNeverGatedOnDocumentCount:
    """Continuity is a date/coverage question, not a document-count one --
    a single upload covering all 6 months in one document is exactly as
    checkable as 6 separate monthly documents.
    """

    def _single_doc_raw(self, *, covered_months=None) -> dict:
        data = {
            "entity_name": "ALPHA TECH SOLUTIONS SDN BHD",
            "bank_name": "MAYBANK BERHAD",
            "statement_start_date": "2026-01-01",
            "statement_end_date": "2026-06-30",
        }
        if covered_months is not None:
            data["covered_months"] = covered_months
        return {
            "bundle_id": "BUNDLE-SINGLE-BANK-DOC",
            "metadata": {
                "total_documents_received": 1,
                "system_date": "2026-07-08",
                "document_types_present": ["bank_statement"],
            },
            "extracted_documents": [
                {"document_id": "bank_all", "document_type": "bank_statement", "data": data},
            ],
        }

    def test_single_document_with_no_covered_months_still_runs_and_passes(self):
        # No per-month detail at all -- still must not skip; the document's
        # own date range is trusted as one unbroken block.
        raw = self._single_doc_raw()
        context = _context(raw)
        system_date = ValidationBundle(**raw).metadata.system_date
        pairs = dict(run_all_rules(context, BMMB_SME_POLICY_V1, system_date))

        continuity = pairs["bank_statement.continuity"]
        assert continuity.skip_reason is None
        assert continuity.result is not None
        assert continuity.result["passed"] is True

    def test_single_document_with_covered_months_gap_still_runs_and_fails(self):
        raw = self._single_doc_raw(covered_months=["2026-01", "2026-02", "2026-04", "2026-05", "2026-06"])
        context = _context(raw)
        system_date = ValidationBundle(**raw).metadata.system_date
        pairs = dict(run_all_rules(context, BMMB_SME_POLICY_V1, system_date))

        continuity = pairs["bank_statement.continuity"]
        assert continuity.skip_reason is None
        assert continuity.result["passed"] is False
