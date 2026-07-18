"""Registry-driven execution of deterministic validation rules.

Each RULE_CATALOG entry (catalog.py) has a matching runner function here. A
runner receives the shared RuleRunContext (document groups + policy + system
date) and decides for itself whether it applies; it returns the outcome(s)
that decision produces. Most rules always produce exactly one outcome, but a
few don't map 1:1 onto a single check:

- financial_statement.freshness/consecutive_years/completeness read from
  financial_statement_docs, or fall back to tax_declaration_docs (Rule 2's
  alternate path for a Sole Prop/Partnership with no audited statements).
- entity_name.match and identity_document.number_match run once per matching
  document/person, not once per bundle, so they can yield any number of
  outcomes (including zero).

run_all_rules() walks RULE_CATALOG in order and calls each rule's runner, so
adding, removing or reordering a rule is a registry change, not a new
if/skip/add branch in engine.py.
"""

from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterator, Optional

from ..domain.context import BundleContext
from ..domain.policies import ValidationPolicy
from .catalog import RULE_CATALOG
from .completeness import (
    check_ic_front_and_back,
    find_missing_ic_documents,
    verify_application_details_completeness,
    verify_consent_signatures,
    verify_financial_sections_present,
    verify_ssm_completeness,
)
from .date_logic import (
    calculate_financial_18_month_rule,
    check_bank_statement_bank_consistency,
    check_bank_statement_continuity,
    check_bank_statement_currency,
    check_bank_statement_freshness,
    check_bank_statement_overdraft,
    check_financial_consecutive_years,
    verify_bank_statement_duration,
)
from .matching import fuzzy_match_entity_names, strict_match_entity_names, strict_match_ic_numbers


@dataclass(frozen=True)
class RuleOutcome:
    """One check result or skip produced by a rule runner.

    ``result`` is the raw dict a rule function returns (validated later by
    validate_rule_result); ``result is None`` means the rule did not apply,
    and ``skip_reason`` explains why.
    """

    check: str
    result: Optional[dict] = None
    skip_reason: Optional[str] = None


@dataclass(frozen=True)
class RuleRunContext:
    bundle_context: BundleContext
    policy: ValidationPolicy
    system_date: date


RuleRunner = Callable[[RuleRunContext], list[RuleOutcome]]


def _bank_statement_periods(docs) -> list[dict]:
    return [
        {"start_date": d.data.statement_start_date.isoformat(), "end_date": d.data.statement_end_date.isoformat()}
        for d in docs
    ]


def _financial_docs(bc: BundleContext):
    """Financial statements, or Rule 2's tax-declaration fallback."""
    return bc.financial_statement_docs or bc.tax_declaration_docs


def _run_ssm_completeness(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.ssm_form_docs:
        return [RuleOutcome("verify_ssm_completeness", skip_reason="No ssm_corporate_form document in bundle.")]
    result = verify_ssm_completeness(
        bc.entity_type, [d.document_subtype for d in bc.ssm_form_docs], policy=ctx.policy,
    )
    return [RuleOutcome("verify_ssm_completeness", result=result)]


def _run_financial_freshness(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    docs = _financial_docs(bc)
    if not docs:
        return [RuleOutcome(
            "calculate_financial_18_month_rule",
            skip_reason="No financial_statement or tax_declaration document in bundle.",
        )]
    latest_fye = max(d.data.financial_year_end for d in docs)
    result = calculate_financial_18_month_rule(
        latest_fye.isoformat(), ctx.system_date.isoformat(),
        max_age_months=ctx.policy.financial_statement_max_age_months,
    )
    return [RuleOutcome("calculate_financial_18_month_rule", result=result)]


def _run_financial_consecutive_years(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    docs = _financial_docs(bc)
    if not docs:
        return [RuleOutcome(
            "check_financial_consecutive_years",
            skip_reason="No financial_statement or tax_declaration document in bundle.",
        )]
    fye_dates = [d.data.financial_year_end.isoformat() for d in docs]
    return [RuleOutcome("check_financial_consecutive_years", result=check_financial_consecutive_years(fye_dates))]


def _run_financial_sections_present(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if bc.financial_statement_docs:
        fs_data = [d.data.model_dump(mode="json") for d in bc.financial_statement_docs]
        return [RuleOutcome("verify_financial_sections_present", result=verify_financial_sections_present(fs_data))]
    if bc.tax_declaration_docs:
        # Borang B is a single tax filing, not a set of financial statements --
        # there's no balance-sheet/P&L/cash-flow/auditor's-report breakdown to verify.
        return [RuleOutcome(
            "verify_financial_sections_present",
            skip_reason="Tax declaration (Borang B) has no financial-section breakdown to verify.",
        )]
    return [RuleOutcome(
        "verify_financial_sections_present",
        skip_reason="No financial_statement or tax_declaration document in bundle.",
    )]


def _run_bank_statement_continuity(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.bank_statement_docs:
        return [RuleOutcome("check_bank_statement_continuity", skip_reason="No bank_statement document in bundle.")]
    if len(bc.bank_statement_docs) < 2:
        return [RuleOutcome(
            "check_bank_statement_continuity",
            skip_reason="Only 1 bank_statement document; continuity needs 2+.",
        )]
    statements = _bank_statement_periods(bc.bank_statement_docs)
    return [RuleOutcome("check_bank_statement_continuity", result=check_bank_statement_continuity(statements))]


def _run_bank_statement_duration(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.bank_statement_docs:
        return [RuleOutcome("verify_bank_statement_duration", skip_reason="No bank_statement document in bundle.")]
    statements = _bank_statement_periods(bc.bank_statement_docs)
    result = verify_bank_statement_duration(statements, bc.entity_type, policy=ctx.policy)
    return [RuleOutcome("verify_bank_statement_duration", result=result)]


def _run_bank_statement_freshness(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.bank_statement_docs:
        return [RuleOutcome("check_bank_statement_freshness", skip_reason="No bank_statement document in bundle.")]
    latest_end_date = max(d.data.statement_end_date for d in bc.bank_statement_docs)
    result = check_bank_statement_freshness(
        latest_end_date.isoformat(), ctx.system_date.isoformat(),
        max_age_months=ctx.policy.bank_statement_max_age_months,
    )
    return [RuleOutcome("check_bank_statement_freshness", result=result)]


def _run_bank_statement_overdraft(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.bank_statement_docs:
        return [RuleOutcome("check_bank_statement_overdraft", skip_reason="No bank_statement document in bundle.")]
    monthly_balances = [
        balance.model_dump(mode="json")
        for d in bc.bank_statement_docs
        for balance in (d.data.monthly_balances or [])
    ]
    if not monthly_balances:
        return [RuleOutcome(
            "check_bank_statement_overdraft",
            skip_reason="No monthly_balances data on any bank_statement document.",
        )]
    return [RuleOutcome("check_bank_statement_overdraft", result=check_bank_statement_overdraft(monthly_balances))]


def _run_bank_statement_bank_consistency(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.bank_statement_docs:
        return [RuleOutcome(
            "check_bank_statement_bank_consistency",
            skip_reason="No bank_statement document in bundle.",
        )]
    bank_names = [d.data.bank_name for d in bc.bank_statement_docs]
    result = check_bank_statement_bank_consistency(bank_names)
    return [RuleOutcome("check_bank_statement_bank_consistency", result=result)]


def _run_bank_statement_currency(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.bank_statement_docs:
        return [RuleOutcome("check_bank_statement_currency", skip_reason="No bank_statement document in bundle.")]
    currencies = [d.data.currency for d in bc.bank_statement_docs]
    result = check_bank_statement_currency(currencies, accepted_currency=ctx.policy.accepted_bank_currency)
    return [RuleOutcome("check_bank_statement_currency", result=result)]


def _run_ic_front_and_back(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.identity_docs:
        return [RuleOutcome("check_ic_front_and_back", skip_reason="No identity_document in bundle.")]
    ic_documents = [d.data.model_dump(mode="json") for d in bc.identity_docs]
    return [RuleOutcome("check_ic_front_and_back", result=check_ic_front_and_back(ic_documents))]


def _run_ic_coverage(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.identity_docs:
        return [RuleOutcome("find_missing_ic_documents", skip_reason="No identity_document in bundle.")]
    ic_documents = [d.data.model_dump(mode="json") for d in bc.identity_docs]
    result = find_missing_ic_documents(bc.ssm_people, ic_documents)
    return [RuleOutcome("find_missing_ic_documents", result=result)]


def _run_consent_signature(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.consent_form_docs:
        return [RuleOutcome("verify_consent_signatures", skip_reason="No consent_form document in bundle.")]
    consent_forms = [d.data.model_dump(mode="json") for d in bc.consent_form_docs]
    result = verify_consent_signatures(bc.ssm_people, consent_forms)
    return [RuleOutcome("verify_consent_signatures", result=result)]


def _run_application_completeness(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    if not bc.customer_info_doc:
        return [RuleOutcome(
            "verify_application_details_completeness",
            skip_reason="No customer_information document in bundle.",
        )]
    result = verify_application_details_completeness(
        bc.customer_info_doc.data.model_dump(mode="json"), policy=ctx.policy,
    )
    return [RuleOutcome("verify_application_details_completeness", result=result)]


def _run_entity_name_match(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    docs = bc.bank_statement_docs + bc.financial_statement_docs + bc.tax_declaration_docs + bc.consent_form_docs
    outcomes = []
    for doc in docs:
        target_name = doc.data.entity_name
        check_name = f"strict_match_entity_names[{doc.document_id}]"
        strict = strict_match_entity_names(bc.entity_name, target_name)
        result = strict if strict["passed"] else fuzzy_match_entity_names(bc.entity_name, target_name)
        outcomes.append(RuleOutcome(check_name, result=result))
    return outcomes


def _run_ic_number_match(ctx: RuleRunContext) -> list[RuleOutcome]:
    bc = ctx.bundle_context
    ic_by_nric = {d.data.nric_passport: d for d in bc.identity_docs}
    outcomes = []
    for person in bc.ssm_people_by_nric.values():
        ic_doc = ic_by_nric.get(person.nric_passport)
        if ic_doc is None:
            continue
        check_name = f"strict_match_ic_numbers[{person.name}]"
        result = strict_match_ic_numbers(person.nric_passport, ic_doc.data.nric_passport)
        outcomes.append(RuleOutcome(check_name, result=result))
    return outcomes


RULE_RUNNERS: dict[str, RuleRunner] = {
    "ssm.document_completeness": _run_ssm_completeness,
    "financial_statement.freshness": _run_financial_freshness,
    "financial_statement.consecutive_years": _run_financial_consecutive_years,
    "financial_statement.completeness": _run_financial_sections_present,
    "bank_statement.continuity": _run_bank_statement_continuity,
    "bank_statement.duration": _run_bank_statement_duration,
    "bank_statement.freshness": _run_bank_statement_freshness,
    "bank_statement.overdraft": _run_bank_statement_overdraft,
    "bank_statement.bank_consistency": _run_bank_statement_bank_consistency,
    "bank_statement.currency": _run_bank_statement_currency,
    "identity_document.front_and_back": _run_ic_front_and_back,
    "identity_document.coverage": _run_ic_coverage,
    "consent.signature": _run_consent_signature,
    "application.completeness": _run_application_completeness,
    "entity_name.match": _run_entity_name_match,
    "identity_document.number_match": _run_ic_number_match,
}


def run_all_rules(context: BundleContext, policy: ValidationPolicy, system_date: date) -> Iterator[tuple[str, RuleOutcome]]:
    """Run every RULE_CATALOG entry, in catalog order, yielding (rule_id, outcome) pairs.

    Catalog order matches the pre-migration engine.py block order exactly, so
    the flattened outcome sequence is identical to what engine.py used to
    build by hand.
    """
    ctx = RuleRunContext(bundle_context=context, policy=policy, system_date=system_date)
    for definition in RULE_CATALOG:
        for outcome in RULE_RUNNERS[definition.rule_id](ctx):
            yield definition.rule_id, outcome
