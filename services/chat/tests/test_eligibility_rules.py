"""Tier-1 eligibility rules — DETERMINISTIC boundary tests (brief §10 Part A).
No LLM: the pure rules.evaluate() decides. Sheet 5 thresholds."""
from services.chat.app.agents.eligibility import rules

FULL = dict(
    business_age_years=4, total_equity_or_net_worth=100_000, revenue=1_000_000,
    working_capital_limit=200_000, end_balance=50_000, staff_count=6,
)


def _status(**overrides):
    return rules.evaluate({**FULL, **overrides}).status


# ── Business age: min 3 years ────────────────────────────────────────────────
def test_business_age_boundaries():
    assert _status(business_age_years=2) == rules.INDICATIVE_NOT_ELIGIBLE
    assert _status(business_age_years=3) == rules.INDICATIVE_ELIGIBLE   # boundary: inclusive
    assert _status(business_age_years=4) == rules.INDICATIVE_ELIGIBLE


# ── Staff: min 5 ─────────────────────────────────────────────────────────────
def test_staff_boundaries():
    assert _status(staff_count=4) == rules.INDICATIVE_NOT_ELIGIBLE
    assert _status(staff_count=5) == rules.INDICATIVE_ELIGIBLE          # boundary
    assert _status(staff_count=6) == rules.INDICATIVE_ELIGIBLE


# ── Working capital: max 30% of revenue ──────────────────────────────────────
def test_working_capital_cap_is_30pct_of_revenue():
    assert _status(revenue=1_000_000, working_capital_limit=300_000) == rules.INDICATIVE_ELIGIBLE   # exactly 30%
    assert _status(revenue=1_000_000, working_capital_limit=300_001) == rules.INDICATIVE_NOT_ELIGIBLE
    assert _status(revenue=1_000_000, working_capital_limit=0) == rules.INDICATIVE_ELIGIBLE


# ── Non-negative floors ──────────────────────────────────────────────────────
def test_zero_floors_pass():
    assert _status(total_equity_or_net_worth=0) == rules.INDICATIVE_ELIGIBLE
    assert _status(end_balance=0) == rules.INDICATIVE_ELIGIBLE


# ── Incomplete drives slot-fill ──────────────────────────────────────────────
def test_missing_slot_is_incomplete():
    r = rules.evaluate({"business_age_years": 4})
    assert r.status == rules.INCOMPLETE
    assert r.next_missing_slot == "total_equity_or_net_worth"
    assert set(r.missing) == set(rules.SLOT_KEYS) - {"business_age_years"}


# ── Tier-2 -> refer to Sales ─────────────────────────────────────────────────
def test_tier2_signal_refers_to_sales():
    assert rules.evaluate(FULL, tier2_signal=True).status == rules.REFER_TO_SALES


# ── Every real verdict carries the disclaimer + rule version ─────────────────
def test_disclaimer_and_version_present():
    r = rules.evaluate(FULL)
    assert r.disclaimer and "not an approval" in r.disclaimer.lower()
    assert r.rule_version == "eligibility_v1"


def test_multiple_failures_listed():
    r = rules.evaluate({**FULL, "business_age_years": 1, "staff_count": 2})
    assert r.status == rules.INDICATIVE_NOT_ELIGIBLE
    assert set(r.failed) == {"business_age_years", "staff_count"}
