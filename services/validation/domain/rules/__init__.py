"""Domain-rule compatibility surface.

The rule implementations still live in ``services.validation.rules`` during
the migration. New application code should import rule contracts and catalog
metadata through this package.
"""

from ...rules import (
    RULE_CATALOG,
    RuleDefinition,
    RuleResult,
    rule_id_for_check,
    validate_rule_result,
)

__all__ = [
    "RULE_CATALOG",
    "RuleDefinition",
    "RuleResult",
    "rule_id_for_check",
    "validate_rule_result",
]
