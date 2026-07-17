"""Shared contract for deterministic rule results.

Rule implementations continue returning plain dictionaries so they remain
JSON-friendly and compatible with the existing agent/tool path. The engine
validates that every rule returns this one shape before adding it to a report.
"""

from typing import Any, Mapping, Optional, TypedDict


class RuleResult(TypedDict):
    passed: Optional[bool]
    message: str
    details: dict[str, Any]


def validate_rule_result(result: Mapping[str, Any]) -> RuleResult:
    """Validate and normalize the public result contract without changing it."""
    required = {"passed", "message", "details"}
    missing = required - set(result)
    if missing:
        raise ValueError(f"Rule result is missing required field(s): {', '.join(sorted(missing))}.")

    passed = result["passed"]
    if passed is not None and not isinstance(passed, bool):
        raise TypeError("Rule result 'passed' must be true, false, or null.")
    if not isinstance(result["message"], str):
        raise TypeError("Rule result 'message' must be a string.")
    if not isinstance(result["details"], dict):
        raise TypeError("Rule result 'details' must be an object.")

    return {
        "passed": passed,
        "message": result["message"],
        "details": result["details"],
    }
