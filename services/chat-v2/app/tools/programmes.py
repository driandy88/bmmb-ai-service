"""
`search_programmes` — which financing programmes fit an amount.

Product selection stays deterministic in v2, exactly as in v1: the requested
amount is tested against each programme's [min, max] quantum range from
products.yaml. The model must not pick products, because the ranges are exact
and a plausible-sounding wrong recommendation is worse than no recommendation.

What IS different: v1 reaches this through a rigid three-question funnel
(purpose, then amount, then result) with each answer parsed by keyword and regex
matching. Here the agent gathers the same two facts conversationally, however the
customer chooses to give them, and calls this once.
"""
from __future__ import annotations

from langchain_core.tools import tool

from app.config.loader import load_config
from app.runtime.context import current


def _fmt_rm(amount: float) -> str:
    if amount >= 1_000_000:
        return f"RM {amount / 1_000_000:.1f}m".replace(".0m", "m")
    if amount >= 1_000:
        return f"RM {amount / 1_000:.0f}k"
    return f"RM {amount:,.0f}"


@tool
def search_programmes(amount_rm: float, purpose: str = "") -> str:
    """Find which SME financing programmes cover a requested amount.

    Call this when the customer tells you how much they need. Which programmes
    are ELIGIBLE is a rules decision and comes from this tool — never add one it
    did not return, and never rule one out that it did. Never quote a programme's
    rate, tenure or conditions from memory; use search_knowledge for details.

    Deciding which of the returned programmes to LEAD WITH is yours. Their full
    names say what they are for — read them against what the customer told you.

    Args:
        amount_rm: the financing amount in ringgit, as a number. Convert what the
            customer said — "500k" is 500000, "1.5 juta" is 1500000.
        purpose: what the financing is for, in the customer's own words
            (e.g. "working capital", "buying a lorry"). Recorded for the audit
            trail; it does not change which programmes come back.
    """
    ctx = current()
    quantum = load_config().products["quantum"]

    # The ONLY rule here: does the requested amount fall inside the programme's
    # quantum range? Ordering used to be done by matching the customer's words
    # against a keyword table (v1) or against option labels (an earlier draft of
    # this file). Both were the same mistake — string matching standing in for
    # understanding, and the exact thing v2 exists to remove. The agent reads
    # "buying a lorry" against "Micro Hire Purchasing Financing" perfectly well.
    eligible = [
        p for p in quantum
        if (p.get("min") is None or amount_rm >= p["min"])
        and (p.get("max") is None or amount_rm <= p["max"])
    ]

    ctx.record("search_programmes", amount_rm=amount_rm, purpose=purpose or None,
               matched=len(eligible))

    if not eligible:
        return (
            f"NO_MATCH: no standard programme covers {_fmt_rm(amount_rm)}. "
            "Do not suggest one anyway — offer the SME financing team, who can "
            "look at options outside the standard quantum table."
        )

    ctx.set_ui(
        "show_program_options",
        step="result",
        amount=amount_rm,
        products=[p["program"] for p in eligible],
    )

    listed = "\n".join(
        f"- {p['program']} — {p.get('full_name', p['program'])}; covers "
        f"{_fmt_rm(p['min']) if p.get('min') else 'any amount'} to "
        f"{_fmt_rm(p['max']) if p.get('max') else 'no stated ceiling'}"
        for p in eligible
    )
    return (
        "DO NOT list these programmes in your reply — the customer's screen is "
        "already showing them as cards, so repeating them reads as clutter. Say in "
        "one or two sentences how many fit and which one or two suit what they told "
        "you, and why. Add that these are indicative and a Bank Muamalat officer "
        f"confirms eligibility on a full application.\n\n"
        f"{len(eligible)} programme(s) cover {_fmt_rm(amount_rm)}:\n{listed}"
    )