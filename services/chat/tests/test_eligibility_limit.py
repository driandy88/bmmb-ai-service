"""Maximum working-capital limit -- deterministic, revenue x a configured pct."""
from app.agents.eligibility.limit import compute_working_capital_limit


def test_computes_pct_of_revenue():
    assert compute_working_capital_limit(1_000_000, 0.30) == 300_000


def test_rounds_to_nearest_thousand():
    assert compute_working_capital_limit(1_234_567, 0.30) == 370_000


def test_none_revenue_is_none():
    assert compute_working_capital_limit(None, 0.30) is None


def test_zero_revenue_is_zero():
    assert compute_working_capital_limit(0, 0.30) == 0
