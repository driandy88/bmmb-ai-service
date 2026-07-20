"""Stable metadata for deterministic validation rules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    check_name: str
    category: str
    description: str
    # The document type this rule's result should be grouped under in
    # ValidationReport.results_by_document. For a rule that only reads one
    # document type, this is that type. For a rule that compares two document
    # types against each other (entity_name.match, identity_document.number_match),
    # this is the document holding the source-of-truth value being checked
    # against (the SSM corporate form), not the document being verified.
    document_group: str


RULE_CATALOG = (
    RuleDefinition("ssm.document_completeness", "verify_ssm_completeness", "ssm", "Required SSM forms are present.", "SSM_CORPORATE_FORM"),
    RuleDefinition("financial_statement.freshness", "calculate_financial_18_month_rule", "financial", "Latest financial year is within the allowed age.", "FINANCIAL_STATEMENT"),
    RuleDefinition("financial_statement.consecutive_years", "check_financial_consecutive_years", "financial", "Financial documents cover two consecutive years.", "FINANCIAL_STATEMENT"),
    RuleDefinition("financial_statement.completeness", "verify_financial_sections_present", "financial", "Each financial statement contains required sections.", "FINANCIAL_STATEMENT"),
    RuleDefinition("bank_statement.continuity", "check_bank_statement_continuity", "bank_statement", "Bank statement periods have no gaps or overlaps.", "BANK_STATEMENT"),
    RuleDefinition("bank_statement.duration", "verify_bank_statement_duration", "bank_statement", "Bank statements meet the required coverage duration.", "BANK_STATEMENT"),
    RuleDefinition("bank_statement.freshness", "check_bank_statement_freshness", "bank_statement", "The latest bank statement is recent enough.", "BANK_STATEMENT"),
    RuleDefinition("bank_statement.overdraft", "check_bank_statement_overdraft", "bank_statement", "Bank statement ending balances are not overdrawn.", "BANK_STATEMENT"),
    RuleDefinition("bank_statement.bank_consistency", "check_bank_statement_bank_consistency", "bank_statement", "All bank statements in the set are from the same bank.", "BANK_STATEMENT"),
    RuleDefinition("bank_statement.currency", "check_bank_statement_currency", "bank_statement", "Bank statement currency matches the accepted currency.", "BANK_STATEMENT"),
    RuleDefinition("identity_document.front_and_back", "check_ic_front_and_back", "identity", "Each IC has front and back images.", "IDENTITY_DOCUMENT"),
    RuleDefinition("identity_document.coverage", "find_missing_ic_documents", "identity", "Required parties have corresponding IC documents.", "IDENTITY_DOCUMENT"),
    RuleDefinition("entity_name.match", "strict_match_entity_names", "matching", "Entity names match across documents.", "SSM_CORPORATE_FORM"),
    RuleDefinition("identity_document.number_match", "strict_match_ic_numbers", "matching", "Identity numbers match across documents.", "SSM_CORPORATE_FORM"),
)

_RULE_ID_BY_CHECK = {definition.check_name: definition.rule_id for definition in RULE_CATALOG}
_DOCUMENT_GROUP_BY_RULE_ID = {definition.rule_id: definition.document_group for definition in RULE_CATALOG}


def rule_id_for_check(check: str) -> str:
    """Return the stable rule ID for a legacy or document-qualified check name."""
    base_check = check.split("[", 1)[0]
    return _RULE_ID_BY_CHECK.get(base_check, base_check)


def document_group_for_rule_id(rule_id: str) -> str:
    """Return the document group a rule's result should be reported under."""
    return _DOCUMENT_GROUP_BY_RULE_ID.get(rule_id, "OTHER")

