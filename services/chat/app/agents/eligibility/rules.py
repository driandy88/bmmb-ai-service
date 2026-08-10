"""
In-principle eligibility — DETERMINISTIC Tier-1 decision (brief §4.4, §12).

This is the ONLY place the eligibility verdict is decided. The LLM extracts the
six self-declared slots and later phrases the explanation; it never calls the
verdict. Thresholds come from config/eligibility_rules.yaml, never hardcoded
here — this function just applies them.

Verdicts:
  INDICATIVE_ELIGIBLE     — all six Tier-1 rules pass.
  INDICATIVE_NOT_ELIGIBLE — at least one Tier-1 rule fails.
  REFER_TO_SALES          — a Tier-2 signal is present (bot must not evaluate it).
  INCOMPLETE              — one or more slots not yet provided (drives slot-fill).

Every real verdict carries the indicative-only disclaimer and logs its inputs
+ rule_version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ...config.loader import AppConfig, load_config

# The six Tier-1 slots (Sheet 5 upper table), in ask order.
SLOT_KEYS = [
    "business_age_years",
    "total_equity_or_net_worth",
    "revenue",
    "working_capital_limit",
    "end_balance",
    "staff_count",
]

INDICATIVE_ELIGIBLE = "INDICATIVE_ELIGIBLE"
INDICATIVE_NOT_ELIGIBLE = "INDICATIVE_NOT_ELIGIBLE"
REFER_TO_SALES = "REFER_TO_SALES"
INCOMPLETE = "INCOMPLETE"


@dataclass
class RuleCheck:
    key: str
    label: str
    value: Optional[float]
    ok: Optional[bool]           # None = slot missing / not yet evaluable
    bound_min: Optional[float]
    bound_max: Optional[float]   # resolved upper bound (e.g. 30% of revenue)
    detail: str


@dataclass
class EligibilityResult:
    status: str
    checks: list[RuleCheck] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    rule_version: str = "eligibility_v1"
    disclaimer: str = ""
    tier2_signal: bool = False

    @property
    def next_missing_slot(self) -> Optional[str]:
        return self.missing[0] if self.missing else None


def _resolve_max(max_spec: Any, inputs: dict) -> Optional[float]:
    """max may be null, a number, or {pct_of: <key>, pct: x} = x * value(key)."""
    if max_spec is None:
        return None
    if isinstance(max_spec, dict) and "pct_of" in max_spec:
        base = inputs.get(max_spec["pct_of"])
        if base is None:
            return None
        return float(max_spec["pct"]) * float(base)
    return float(max_spec)


def evaluate(
    inputs: dict,
    *,
    tier2_signal: bool = False,
    config: Optional[AppConfig] = None,
) -> EligibilityResult:
    cfg = config or load_config()
    elig = cfg.eligibility
    tier1 = elig["tier1"]

    checks: list[RuleCheck] = []
    missing: list[str] = []
    failed: list[str] = []

    for rule in tier1:
        key = rule["key"]
        value = inputs.get(key)
        bound_min = float(rule["min"]) if rule.get("min") is not None else None
        bound_max = _resolve_max(rule.get("max"), inputs)

        if value is None:
            checks.append(RuleCheck(key, rule["label"], None, None, bound_min, bound_max, "not provided"))
            missing.append(key)
            continue

        value = float(value)
        ok = True
        reasons = []
        if bound_min is not None and value < bound_min:
            ok = False
            reasons.append(f"below minimum {bound_min:g}")
        if bound_max is not None and value > bound_max:
            ok = False
            reasons.append(f"above maximum {bound_max:g}")
        detail = "; ".join(reasons) if reasons else "within range"
        checks.append(RuleCheck(key, rule["label"], value, ok, bound_min, bound_max, detail))
        if not ok:
            failed.append(key)

    # Status precedence: Tier-2 referral > incomplete > fail > pass.
    if tier2_signal:
        status = REFER_TO_SALES
    elif missing:
        status = INCOMPLETE
    elif failed:
        status = INDICATIVE_NOT_ELIGIBLE
    else:
        status = INDICATIVE_ELIGIBLE

    return EligibilityResult(
        status=status,
        checks=checks,
        missing=missing,
        failed=failed,
        rule_version=cfg.rule_version,
        disclaimer=str(elig.get("disclaimer", "")).strip(),
        tier2_signal=tier2_signal,
    )
