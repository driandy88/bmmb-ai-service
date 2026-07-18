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

        ssm_outcomes = [outcome for rule_id, outcome in pairs if rule_id == "ssm.document_completeness"]
        assert len(ssm_outcomes) == 1
        assert ssm_outcomes[0].result is not None
        assert ssm_outcomes[0].skip_reason is None

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

        ssm_outcomes = [outcome for rule_id, outcome in pairs if rule_id == "ssm.document_completeness"]
        assert len(ssm_outcomes) == 1
        assert ssm_outcomes[0].result is None
        assert ssm_outcomes[0].skip_reason == "No ssm_corporate_form document in bundle."

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
