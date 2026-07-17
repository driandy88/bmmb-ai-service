"""Stable metadata for deterministic validation rules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    check_name: str
    category: str
    description: str


RULE_CATALOG = (
    RuleDefinition("ssm.document_completeness", "verify_ssm_completeness", "ssm", "Required SSM forms are present."),
    RuleDefinition("financial_statement.freshness", "calculate_financial_18_month_rule", "financial", "Latest financial year is within the allowed age."),
    RuleDefinition("financial_statement.consecutive_years", "check_financial_consecutive_years", "financial", "Financial documents cover two consecutive years."),
    RuleDefinition("financial_statement.completeness", "verify_financial_sections_present", "financial", "Each financial statement contains required sections."),
    RuleDefinition("bank_statement.continuity", "check_bank_statement_continuity", "bank_statement", "Bank statement periods have no gaps or overlaps."),
    RuleDefinition("bank_statement.duration", "verify_bank_statement_duration", "bank_statement", "Bank statements meet the required coverage duration."),
    RuleDefinition("bank_statement.freshness", "check_bank_statement_freshness", "bank_statement", "The latest bank statement is recent enough."),
    RuleDefinition("bank_statement.overdraft", "check_bank_statement_overdraft", "bank_statement", "Bank statement ending balances are not overdrawn."),
    RuleDefinition("bank_statement.bank_consistency", "check_bank_statement_bank_consistency", "bank_statement", "All bank statements in the set are from the same bank."),
    RuleDefinition("bank_statement.currency", "check_bank_statement_currency", "bank_statement", "Bank statement currency matches the accepted currency."),
    RuleDefinition("identity_document.front_and_back", "check_ic_front_and_back", "identity", "Each IC has front and back images."),
    RuleDefinition("identity_document.coverage", "find_missing_ic_documents", "identity", "Required parties have corresponding IC documents."),
    RuleDefinition("consent.signature", "verify_consent_signatures", "consent", "Required parties have signed consent forms."),
    RuleDefinition("application.completeness", "verify_application_details_completeness", "application", "Mandatory application fields are completed."),
    RuleDefinition("entity_name.match", "strict_match_entity_names", "matching", "Entity names match across documents."),
    RuleDefinition("identity_document.number_match", "strict_match_ic_numbers", "matching", "Identity numbers match across documents."),
)

_RULE_ID_BY_CHECK = {definition.check_name: definition.rule_id for definition in RULE_CATALOG}


def rule_id_for_check(check: str) -> str:
    """Return the stable rule ID for a legacy or document-qualified check name."""
    base_check = check.split("[", 1)[0]
    return _RULE_ID_BY_CHECK.get(base_check, base_check)
    
