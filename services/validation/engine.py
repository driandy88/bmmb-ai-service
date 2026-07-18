"""
Deterministic BMMB validation rule engine.

Packaged as a single reusable ValidationEngine so both the plain Python
path and the agentic path (agent.py's run_deterministic_checks tool) can
call it identically.

The engine only catches what a check was explicitly written to look for. It
has no visibility into pre-adapter raw extraction data, so it cannot detect
adapter/mapping bugs (e.g. a consent form's real signatory data ending up in
the wrong field) — it just sees whatever the canonical bundle says, correct
or not. That blind spot is exactly what the agentic path (agent.py) exists
to cover; see examples/test_conflict_example.py for a concrete demonstration.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, computed_field

from .bundle import ValidationBundle
from .domain.context import BundleContext
from .domain.policies import BMMB_SME_POLICY_V1, ValidationPolicy
from .rules import document_group_for_rule_id, run_all_rules, validate_rule_result


class ValidationStatus(str, Enum):
    """Explicit status values used by deterministic validation checks.

    ``passed`` is kept separately on ``CheckResult`` for backwards
    compatibility with existing API consumers.  ``status`` removes the
    ambiguity of ``passed=None``: a rule can be genuinely not applicable, or
    it can require human review because extraction was inconclusive.
    """

    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    NOT_APPLICABLE = "not_applicable"


def _status_for_passed(passed: Optional[bool]) -> ValidationStatus:
    if passed is True:
        return ValidationStatus.PASSED
    if passed is False:
        return ValidationStatus.FAILED
    return ValidationStatus.NEEDS_REVIEW


class CheckResult(BaseModel):
    rule_id: str
    check: str
    passed: Optional[bool]  # None means "not applicable" — skipped, not failed
    status: ValidationStatus
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    entity_name: str
    entity_type: str
    results: List[CheckResult]
    policy_id: str = BMMB_SME_POLICY_V1.policy_id

    @property
    def overall_passed(self) -> bool:
        return all(r.passed is not False for r in self.results)

    @property
    def overall_status(self) -> ValidationStatus:
        """Aggregate status while preserving the existing pass semantics.

        A failed check wins over needs-review.  Not-applicable checks do not
        change the result, matching the existing ``overall_passed`` behavior.
        """
        if any(r.status is ValidationStatus.FAILED for r in self.results):
            return ValidationStatus.FAILED
        if any(r.status is ValidationStatus.NEEDS_REVIEW for r in self.results):
            return ValidationStatus.NEEDS_REVIEW
        return ValidationStatus.PASSED

    @computed_field
    @property
    def results_by_document(self) -> Dict[str, List[CheckResult]]:
        """`results`, the same CheckResult objects, grouped by document type.

        A rule that compares two document types (entity_name.match,
        identity_document.number_match) is grouped under the document
        holding the source-of-truth value being checked against -- see
        RuleDefinition.document_group in rules/catalog.py -- not under every
        document type it happens to touch.
        """
        grouped: Dict[str, List[CheckResult]] = {}
        for result in self.results:
            grouped.setdefault(document_group_for_rule_id(result.rule_id), []).append(result)
        return grouped


class ValidationEngine:
    """Runs every applicable rules/ check against a validated bundle.

    Rule applicability, argument-binding and execution live in
    rules.registry (RULE_CATALOG-driven); this method's only job is turning
    each (rule_id, RuleOutcome) pair into a CheckResult.
    """

    def __init__(self, policy: ValidationPolicy = BMMB_SME_POLICY_V1):
        self.policy = policy

    def run(self, bundle: ValidationBundle) -> ValidationReport:
        context = BundleContext.from_bundle(bundle)
        system_date = bundle.metadata.system_date

        results: List[CheckResult] = []
        for rule_id, outcome in run_all_rules(context, self.policy, system_date):
            if outcome.result is not None:
                result = validate_rule_result(outcome.result)
                results.append(
                    CheckResult(
                        rule_id=rule_id,
                        check=outcome.check,
                        passed=result["passed"],
                        status=_status_for_passed(result["passed"]),
                        message=result["message"],
                        details=result["details"],
                    )
                )
            else:
                results.append(
                    CheckResult(
                        rule_id=rule_id,
                        check=outcome.check,
                        passed=None,
                        status=ValidationStatus.NOT_APPLICABLE,
                        message=outcome.skip_reason,
                        details={},
                    )
                )

        return ValidationReport(
            entity_name=context.entity_name,
            entity_type=context.entity_type,
            results=results,
            policy_id=self.policy.policy_id,
        )
