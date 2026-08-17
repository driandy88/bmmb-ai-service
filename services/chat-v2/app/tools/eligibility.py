"""
`check_eligibility` — the deterministic verdict, unchanged from v1.

BMMB froze the typed eligibility funnel: the bot does not interview customers
about their financials, and a fresh "do I qualify?" goes to the SME team. That
policy is stated in `prompts/40_conversation.md` and enforced here as well — the
tool refuses to run without figures, so a model that ignored the prompt still
cannot start an assessment.

The rules engine itself is vendored v1 code (`agents/eligibility/rules.py`),
byte-identical. The model never decides a verdict; it relays one. The disclaimer
comes back verbatim because it is compliance wording, not phrasing.

Slots normally arrive from a document upload, mapped upstream. Passing them as
explicit arguments keeps the tool usable directly by the eval harness too.
"""
from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool

from app.agents.eligibility import rules
from app.config.loader import load_config
from app.runtime.context import current


@tool
def check_eligibility(
    business_age_years: Optional[float] = None,
    total_equity_or_net_worth: Optional[float] = None,
    revenue: Optional[float] = None,
    working_capital_limit: Optional[float] = None,
    end_balance: Optional[float] = None,
    staff_count: Optional[float] = None,
) -> str:
    """Run the indicative eligibility rules over figures ALREADY on file.

    Only call this when figures were extracted from a document the customer
    uploaded. Do NOT call it to start an assessment, and do not ask the customer
    for these numbers in chat — BMMB policy is that the SME financing team does
    the real check. If someone asks whether they qualify and you have no figures,
    explain the criteria in general terms and hand off instead.

    The result is indicative only. Pass on its disclaimer word for word.

    Args:
        business_age_years: years the business has been operating
        total_equity_or_net_worth: total equity or net worth, RM
        revenue: annual revenue or turnover, RM
        working_capital_limit: working capital financing sought, RM
        end_balance: current bank end balance, RM
        staff_count: number of staff employed
    """
    ctx = current()
    slots = {
        "business_age_years": business_age_years,
        "total_equity_or_net_worth": total_equity_or_net_worth,
        "revenue": revenue,
        "working_capital_limit": working_capital_limit,
        "end_balance": end_balance,
        "staff_count": staff_count,
    }
    provided = {k: v for k, v in slots.items() if v is not None}

    if not provided:
        ctx.record("check_eligibility", result="refused_no_figures")
        return (
            "NO_FIGURES: nothing on file to assess, and you must not ask the customer "
            "for their financials. Explain the general criteria if useful, then offer "
            "the SME financing team to do the real check."
        )

    result = rules.evaluate(provided, config=load_config())

    # Outcomes only — the raw figures never reach the audit record. Same PII
    # discipline as v1: {revenue_ok: false}, never the revenue.
    ctx.record(
        "check_eligibility",
        status=result.status,
        rule_results={c.key: c.ok for c in result.checks},
        missing=result.missing,
        rule_version=result.rule_version,
    )

    if result.status == rules.INCOMPLETE:
        return (
            f"INCOMPLETE: the document did not provide {', '.join(result.missing)}. "
            "Do not ask the customer for the missing figures — offer the SME "
            "financing team to complete the check."
        )

    if result.status == rules.REFER_TO_SALES:
        return "REFER_TO_SALES: this case needs a human assessment. Hand off to the SME financing team."

    passed = result.status == rules.INDICATIVE_ELIGIBLE
    ctx.set_ui("show_eligibility_result", outcome="PASS" if passed else "FAIL")

    if passed:
        return (
            "INDICATIVE_ELIGIBLE — the figures on file meet the initial criteria. "
            "Next step is a full review by the SME financing team.\n"
            f"Include this disclaimer verbatim: {result.disclaimer}"
        )

    unmet = [
        f"{c.label.lower()} (needs at least {c.bound_min:g})" if c.bound_min is not None
        else f"{c.label.lower()} (above the indicative limit of {c.bound_max:g})"
        for c in result.checks if c.ok is False
    ]
    return (
        "INDICATIVE_NOT_ELIGIBLE — criteria not met: "
        + ("; ".join(unmet) if unmet else "some initial criteria")
        + ".\nThese criteria are public, so you may explain which were not met. Do not "
        "suggest how to change the figures to pass.\n"
        f"Include this disclaimer verbatim: {result.disclaimer}"
    )