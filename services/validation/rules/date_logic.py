"""
Date-logic validation tools for BMMB document bundle checks.

Each function takes plain, JSON-friendly inputs (dates as ISO 'YYYY-MM-DD'
strings or `datetime.date`) and returns a JSON-serializable dict:

    {
        "passed": bool,
        "message": str,
        "details": {...}   # rule-specific supporting numbers
    }

This shape is meant to be handed straight back to an LLM agent as a tool
result, so keep it flat and self-explanatory rather than raising exceptions
for rule failures (raise only for malformed input).

Docstrings are written Google-style (with an Args: section) because the
Gemini function-calling binding sends the whole docstring as the tool's
description verbatim; per-argument text lives here, not in a separate
schema field.
"""

from datetime import timedelta
from typing import Dict, List

from dateutil.relativedelta import relativedelta

from ._utils import to_date

# NOTE: date parameters are typed as `str` (ISO 'YYYY-MM-DD'), not
# `datetime.date`, and nested objects as `Dict[str, object]`, not TypedDict
# or `Dict[str, Any]`. Gemini's automatic function-calling *schema
# generation* accepts `date`, TypedDict, and `Any` fine, but its *argument
# execution* does not: `datetime.date` raises "argument value ... is not
# compatible with parameter annotation <class 'datetime.date'>"; TypedDict
# raises "TypedDict does not support instance and class checks"; a bare
# (unparameterized) `Dict` raises "not enough values to unpack (expected 2,
# got 0)" since the SDK calls typing.get_args() expecting a (key_type,
# value_type) pair; and `Dict[str, Any]` raises "typing.Any cannot be used
# with isinstance()" since the SDK isinstance-checks each value against the
# value type. `Dict[str, object]` is the only combination that survives
# both schema generation and execution, at the cost of a looser schema.

# Minimum bank statement coverage, in months, by entity type.
_MIN_STATEMENT_MONTHS_BY_ENTITY = {
    "sole prop": 12,
    "sole proprietor": 12,
    "sole proprietorship": 12,
}
_DEFAULT_MIN_STATEMENT_MONTHS = 6  # Sdn Bhd / partnership / anything else


def calculate_financial_18_month_rule(latest_fye_date: str, system_date: str) -> Dict:
    """Check the BMMB rule that the latest financial statement must not be older than 18 months.

    Use this when a financial_statement document has been extracted and you
    need to confirm it is still "fresh" enough to be accepted, relative to
    the bundle's system date.

    Args:
        latest_fye_date: Financial year end (FYE) date of the most recent
            financial statement on file, as an ISO 'YYYY-MM-DD' date.
        system_date: The current system/application date, as an ISO
            'YYYY-MM-DD' date.
    """
    fye = to_date(latest_fye_date)
    today = to_date(system_date)

    if fye > today:
        return {
            "passed": False,
            "message": "Financial year end date is in the future relative to system date.",
            "details": {
                "latest_fye_date": fye.isoformat(),
                "system_date": today.isoformat(),
            },
        }

    rd = relativedelta(today, fye)
    months_elapsed = rd.years * 12 + rd.months
    if rd.days > 0:
        # Any leftover days push the FYE past the whole-month mark, so
        # count conservatively (round up against the applicant).
        months_elapsed += 1

    deadline = fye + relativedelta(months=18)
    passed = months_elapsed <= 18

    return {
        "passed": passed,
        "message": (
            f"Latest financial statement is {months_elapsed} month(s) old "
            f"({'within' if passed else 'exceeds'} the 18-month limit)."
        ),
        "details": {
            "latest_fye_date": fye.isoformat(),
            "system_date": today.isoformat(),
            "months_elapsed": months_elapsed,
            "expiry_deadline": deadline.isoformat(),
        },
    }


def check_financial_consecutive_years(fye_dates: List[str]) -> Dict:
    """Check that exactly 2 financial statements are provided, covering 2 continuous years.

    Use this when the bundle contains financial_statement documents and you
    need to confirm they form an unbroken 2-year run (e.g. FYE 2024-12-31 and
    FYE 2025-12-31), with no missing year and no duplicate year.

    Args:
        fye_dates: Financial year end dates, one per financial statement
            document, as ISO 'YYYY-MM-DD' dates. Must contain exactly 2
            dates.
    """
    dates = sorted(to_date(d) for d in fye_dates)

    if len(dates) != 2:
        return {
            "passed": False,
            "message": f"Expected exactly 2 financial year end dates, got {len(dates)}.",
            "details": {"fye_dates": [d.isoformat() for d in dates]},
        }

    earlier, later = dates
    if earlier == later:
        return {
            "passed": False,
            "message": "Duplicate financial year end date supplied.",
            "details": {"fye_dates": [d.isoformat() for d in dates]},
        }

    rd = relativedelta(later, earlier)
    passed = rd.years == 1 and rd.months == 0 and rd.days == 0

    return {
        "passed": passed,
        "message": (
            "Financial statements cover 2 continuous years."
            if passed
            else f"Gap detected between financial years: {rd.years}y {rd.months}m {rd.days}d apart."
        ),
        "details": {
            "fye_dates": [d.isoformat() for d in dates],
            "gap_years": rd.years,
            "gap_months": rd.months,
            "gap_days": rd.days,
        },
    }


def check_bank_statement_continuity(statements: List[Dict[str, object]]) -> Dict:
    """Sort bank statements by date and check there are no missing or overlapping days between them.

    Use this whenever the bundle contains 2 or more bank_statement documents,
    before trusting their combined date range for anything else (e.g. before
    calling verify_bank_statement_duration).

    Args:
        statements: One entry per bank_statement document, each with
            start_date and end_date as ISO 'YYYY-MM-DD' dates covering that
            statement's period.
    """
    parsed = sorted(
        (
            {
                "start_date": to_date(s["start_date"]),
                "end_date": to_date(s["end_date"]),
            }
            for s in statements
        ),
        key=lambda s: s["start_date"],
    )

    issues = []
    for prev, curr in zip(parsed, parsed[1:]):
        expected_start = prev["end_date"] + timedelta(days=1)
        if curr["start_date"] > expected_start:
            gap_days = (curr["start_date"] - expected_start).days
            issues.append(
                {
                    "type": "gap",
                    "between": [prev["end_date"].isoformat(), curr["start_date"].isoformat()],
                    "missing_days": gap_days,
                }
            )
        elif curr["start_date"] < expected_start:
            overlap_days = (expected_start - curr["start_date"]).days
            issues.append(
                {
                    "type": "overlap",
                    "between": [prev["end_date"].isoformat(), curr["start_date"].isoformat()],
                    "overlap_days": overlap_days,
                }
            )

    passed = len(issues) == 0

    return {
        "passed": passed,
        "message": (
            "Bank statements are continuous with no gaps or overlaps."
            if passed
            else f"Found {len(issues)} continuity issue(s) in bank statements."
        ),
        "details": {
            "statements_sorted": [
                {"start_date": s["start_date"].isoformat(), "end_date": s["end_date"].isoformat()}
                for s in parsed
            ],
            "issues": issues,
        },
    }


def verify_bank_statement_duration(statements: List[Dict[str, object]], entity_type: str) -> Dict:
    """Check total consecutive months of bank statements against the BMMB minimum for the entity type.

    BMMB requires 6 months of statements for a Sdn Bhd (or other company) and
    12 months for a Sole Proprietor. Use this after (or instead of, since it
    checks continuity internally) check_bank_statement_continuity, once you
    know the entity_type from the SSM form.

    Args:
        statements: One entry per bank_statement document, each with
            start_date and end_date as ISO 'YYYY-MM-DD' dates.
        entity_type: The entity type from the SSM corporate form, e.g.
            "Sdn Bhd" or "Sole Proprietor".
    """
    continuity = check_bank_statement_continuity(statements)
    if not continuity["passed"]:
        return {
            "passed": False,
            "message": "Cannot verify duration: bank statements are not continuous.",
            "details": continuity["details"],
        }

    sorted_statements = continuity["details"]["statements_sorted"]
    earliest_start = to_date(sorted_statements[0]["start_date"])
    latest_end = to_date(sorted_statements[-1]["end_date"])

    rd = relativedelta(latest_end, earliest_start)
    months_covered = rd.years * 12 + rd.months
    if rd.days > 0:
        # A partial trailing month still counts as a covered month.
        months_covered += 1

    min_required = _MIN_STATEMENT_MONTHS_BY_ENTITY.get(
        entity_type.strip().lower(), _DEFAULT_MIN_STATEMENT_MONTHS
    )
    passed = months_covered >= min_required

    return {
        "passed": passed,
        "message": (
            f"Bank statements cover {months_covered} month(s); "
            f"minimum required for '{entity_type}' is {min_required} month(s)."
        ),
        "details": {
            "entity_type": entity_type,
            "months_covered": months_covered,
            "minimum_required_months": min_required,
            "earliest_start": earliest_start.isoformat(),
            "latest_end": latest_end.isoformat(),
        },
    }


def validate_form_d_expiry(expiry_date: str, tenure_months: int, system_date: str) -> Dict:
    """Check that the SSM Form D validity period covers the requested financing tenure.

    Use this when the bundle includes an ssm_corporate_form with
    document_subtype "form_d", together with the tenure_months from the
    customer_information document.

    Args:
        expiry_date: Expiry date printed on Form D, as an ISO 'YYYY-MM-DD'
            date.
        tenure_months: The requested financing tenure, in months, from the
            customer_information document.
        system_date: The current system/application date, as an ISO
            'YYYY-MM-DD' date.
    """
    expiry = to_date(expiry_date)
    today = to_date(system_date)

    required_coverage_end = today + relativedelta(months=tenure_months)
    passed = expiry >= required_coverage_end
    shortfall_days = 0 if passed else (required_coverage_end - expiry).days

    return {
        "passed": passed,
        "message": (
            f"Form D expiry ({expiry.isoformat()}) covers the {tenure_months}-month tenure."
            if passed
            else (
                f"Form D expiry ({expiry.isoformat()}) is short of the required "
                f"coverage end date ({required_coverage_end.isoformat()}) by {shortfall_days} day(s)."
            )
        ),
        "details": {
            "expiry_date": expiry.isoformat(),
            "system_date": today.isoformat(),
            "tenure_months": tenure_months,
            "required_coverage_end": required_coverage_end.isoformat(),
            "shortfall_days": shortfall_days,
        },
    }


def months_between(start: str, end: str) -> Dict:
    """Compute the whole-month gap between two ISO 'YYYY-MM-DD' dates (a general-purpose date helper).

    Use this for any date-arithmetic question that doesn't map to one of the
    specific BMMB rule tools above — e.g. sanity-checking a gap you noticed
    while investigating raw extraction data.

    Args:
        start: Earlier date, as an ISO 'YYYY-MM-DD' date.
        end: Later date, as an ISO 'YYYY-MM-DD' date.
    """
    start_date = to_date(start)
    end_date = to_date(end)
    rd = relativedelta(end_date, start_date)
    months = rd.years * 12 + rd.months

    return {
        "passed": True,
        "message": f"{start_date.isoformat()} to {end_date.isoformat()} spans {months} whole month(s), {rd.days} extra day(s).",
        "details": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "months": months,
            "extra_days": rd.days,
        },
    }
