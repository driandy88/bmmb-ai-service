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

from datetime import date
from typing import Dict, List, Optional, Tuple

from dateutil.relativedelta import relativedelta

from ._utils import best_fuzzy_match, resolve_entity_type_key, to_date
from ..domain.policies import BMMB_SME_POLICY_V1, ValidationPolicy

MonthKey = Tuple[int, int]  # (year, month)


def _month_key_str(month: MonthKey) -> str:
    year, mon = month
    return f"{year:04d}-{mon:02d}"


def _next_month(month: MonthKey) -> MonthKey:
    year, mon = month
    return (year + 1, 1) if mon == 12 else (year, mon + 1)


def _months_in_range(start: date, end: date) -> List[MonthKey]:
    """Every (year, month) from start's month to end's month, inclusive."""
    months = []
    current = (start.year, start.month)
    last = (end.year, end.month)
    while current <= last:
        months.append(current)
        current = _next_month(current)
    return months


def _covered_months_for_statement(statement: Dict[str, object]) -> List[MonthKey]:
    """The (year, month) values a statement actually has data for.

    Uses the statement's own `covered_months` (ISO 'YYYY-MM' strings) when
    given -- this is the only way to see a gap *inside* one consolidated
    document. Otherwise falls back to expanding start_date..end_date, which
    is exactly right for a single physical statement (its own period can't
    have an internal gap) but would hide one for a document that's actually
    a merge of several files.
    """
    raw_months = statement.get("covered_months")
    if raw_months:
        return [tuple(int(part) for part in str(m).split("-")) for m in raw_months]
    return _months_in_range(to_date(statement["start_date"]), to_date(statement["end_date"]))

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

def calculate_financial_18_month_rule(
    latest_fye_date: str,
    system_date: str,
    max_age_months: int = BMMB_SME_POLICY_V1.financial_statement_max_age_months,
) -> Dict:
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

    deadline = fye + relativedelta(months=max_age_months)
    passed = months_elapsed <= max_age_months

    return {
        "passed": passed,
        "message": (
            f"Latest financial statement is {months_elapsed} month(s) old "
            f"({'within' if passed else 'exceeds'} the {max_age_months}-month limit)."
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
    """Check that the calendar months covered by all bank statements have no gaps or overlaps.

    Use this whenever the bundle contains bank_statement document(s), before
    trusting their combined coverage for anything else (e.g. before calling
    verify_bank_statement_duration). Works whether the statements arrived as
    several separate documents or one already-consolidated document -- see
    covered_months below.

    Args:
        statements: One entry per bank_statement document, each with
            start_date and end_date as ISO 'YYYY-MM-DD' dates covering that
            statement's period, and optionally covered_months (a list of
            ISO 'YYYY-MM' strings for the calendar months it actually has
            transaction data for -- needed to catch a gap *inside* a single
            consolidated document; without it, the document's own date range
            is trusted as one unbroken block).
    """
    month_occurrences: Dict[MonthKey, int] = {}
    for statement in statements:
        for month in set(_covered_months_for_statement(statement)):
            month_occurrences[month] = month_occurrences.get(month, 0) + 1

    covered = sorted(month_occurrences)
    expected = _months_in_range(date(*covered[0], 1), date(*covered[-1], 1)) if covered else []

    issues = []
    missing = [m for m in expected if m not in month_occurrences]
    if missing:
        issues.append({"type": "gap", "missing_months": [_month_key_str(m) for m in missing]})
    overlapping = sorted(m for m, count in month_occurrences.items() if count > 1)
    if overlapping:
        issues.append({"type": "overlap", "overlapping_months": [_month_key_str(m) for m in overlapping]})

    passed = len(issues) == 0

    return {
        "passed": passed,
        "message": (
            "Bank statements are continuous with no gaps or overlaps."
            if passed
            else f"Found {len(issues)} continuity issue(s) in bank statements."
        ),
        "details": {
            "covered_months": [_month_key_str(m) for m in covered],
            "issues": issues,
        },
    }


def verify_bank_statement_duration(
    statements: List[Dict[str, object]],
    entity_type: str,
    policy: ValidationPolicy = BMMB_SME_POLICY_V1,
) -> Dict:
    """Check total consecutive months of bank statements against the BMMB minimum for the entity type.

    BMMB requires 6 months of statements for a Sdn Bhd (or other company) and
    12 months for a Sole Proprietor. Use this after (or instead of, since it
    checks continuity internally) check_bank_statement_continuity, once you
    know the entity_type from the SSM form.

    Args:
        statements: One entry per bank_statement document, each with
            start_date and end_date as ISO 'YYYY-MM-DD' dates.
        entity_type: The entity type from the SSM corporate form, e.g.
            "Sdn Bhd" or "Sole Proprietor". Typos, alternate spellings, and
            Malay terms (e.g. "Perniagaan Tunggal", "Enterprise",
            "Perkongsian", "llp") are resolved to the correct requirement via
            an alias table and a conservative fuzzy-match fallback, rather
            than silently defaulting to the Sdn-Bhd-shaped minimum.
    """
    continuity = check_bank_statement_continuity(statements)
    if not continuity["passed"]:
        return {
            "passed": False,
            "message": "Cannot verify duration: bank statements are not continuous.",
            "details": continuity["details"],
        }

    earliest_start = to_date(min(s["start_date"] for s in statements))
    latest_end = to_date(max(s["end_date"] for s in statements))

    # continuity has already confirmed covered_months forms one unbroken run,
    # so its size is exactly the number of calendar months covered --
    # immune to the day-of-month alignment that relativedelta arithmetic
    # (e.g. Jan 31 -> Jun 30 has a zero day-remainder) used to undercount by one.
    months_covered = len(continuity["details"]["covered_months"])

    resolved_key = resolve_entity_type_key(entity_type, policy)
    min_required = (
        policy.minimum_bank_statement_months_by_entity[resolved_key]
        if resolved_key is not None
        else policy.default_minimum_bank_statement_months
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
            "resolved_entity_type_key": resolved_key,
            "months_covered": months_covered,
            "minimum_required_months": min_required,
            "earliest_start": earliest_start.isoformat(),
            "latest_end": latest_end.isoformat(),
        },
    }


def check_bank_statement_freshness(
    latest_end_date: str,
    system_date: str,
    max_age_months: int = BMMB_SME_POLICY_V1.bank_statement_max_age_months,
) -> Dict:
    """Check that the most recent bank statement is recent enough to be accepted (not outdated).

    Use this once you know the latest statement_end_date across every
    bank_statement document in the bundle. Bank statements go stale much
    faster than financial statements, so the allowed age is much shorter
    than the 18-month financial statement rule.

    Args:
        latest_end_date: The latest statement_end_date across all
            bank_statement documents in the bundle, as an ISO 'YYYY-MM-DD'
            date.
        system_date: The current system/application date, as an ISO
            'YYYY-MM-DD' date.
    """
    latest = to_date(latest_end_date)
    today = to_date(system_date)

    deadline = latest + relativedelta(months=max_age_months)
    passed = today <= deadline

    return {
        "passed": passed,
        "message": (
            f"Latest bank statement (ending {latest.isoformat()}) is within the "
            f"{max_age_months}-month freshness window."
            if passed
            else f"Latest bank statement (ending {latest.isoformat()}) is older than the "
            f"{max_age_months}-month freshness window."
        ),
        "details": {
            "latest_statement_end_date": latest.isoformat(),
            "system_date": today.isoformat(),
            "max_age_months": max_age_months,
            "freshness_deadline": deadline.isoformat(),
        },
    }


def check_bank_statement_overdraft(monthly_balances: List[Dict[str, object]]) -> Dict:
    """Check that every monthly bank statement end balance is positive (not overdrawn).

    Use this for the combined monthly_balances of every bank_statement
    document in the bundle. A negative end balance (or a balance shown in
    parentheses, which is how negatives are often printed) on a debit
    (current/savings) account means the account was overdrawn that month.

    Args:
        monthly_balances: One entry per statement month, each with month
            (e.g. "July 2023") and end_balance (a number; already negative
            if the account was overdrawn).
    """
    overdrawn = [
        {"month": m.get("month"), "end_balance": m.get("end_balance")}
        for m in monthly_balances
        if m.get("end_balance") is not None and m["end_balance"] < 0
    ]
    passed = len(overdrawn) == 0

    return {
        "passed": passed,
        "message": (
            "No overdrawn months found across the bank statements."
            if passed
            else f"{len(overdrawn)} month(s) show a negative (overdrawn) end balance."
        ),
        "details": {
            "months_checked": len(monthly_balances),
            "overdrawn_months": overdrawn,
        },
    }


# Legal/jurisdiction words Malaysian banks routinely append and that carry no
# distinguishing identity on their own (e.g. "Maybank Berhad", "Maybank Bhd",
# "AmBank (M) Berhad", "HSBC Bank Malaysia Berhad" are all one bank). Stripped
# before both the alias lookup and the fuzzy fallback below.
_BANK_LEGAL_SUFFIX_WORDS = {"BERHAD", "BHD", "MALAYSIA", "M"}

# Known Malaysian bank name variants (full legal name minus the legal-suffix
# words above, common abbreviations), normalized-form -> canonical identity.
# Extend this table first when a new variant shows up; it's the
# predictable, auditable path. Fuzzy matching (_canonical_bank_name) is only
# a fallback for spelling variants this table hasn't seen yet.
_BANK_NAME_ALIASES: Dict[str, str] = {
    "MAYBANK": "MAYBANK",
    "MALAYAN BANKING": "MAYBANK",
    "CIMB": "CIMB BANK",
    "CIMB BANK": "CIMB BANK",
    "PUBLIC BANK": "PUBLIC BANK",
    "RHB": "RHB BANK",
    "RHB BANK": "RHB BANK",
    "HONG LEONG BANK": "HONG LEONG BANK",
    "AMBANK": "AMBANK",
    "AM BANK": "AMBANK",
    "BANK ISLAM": "BANK ISLAM",
    "BANK RAKYAT": "BANK RAKYAT",
    "BANK KERJASAMA RAKYAT": "BANK RAKYAT",
    "BANK MUAMALAT": "BANK MUAMALAT",
    "OCBC": "OCBC BANK",
    "OCBC BANK": "OCBC BANK",
    "HSBC": "HSBC BANK",
    "HSBC BANK": "HSBC BANK",
    "STANDARD CHARTERED": "STANDARD CHARTERED",
    "STANDARD CHARTERED BANK": "STANDARD CHARTERED",
    "UOB": "UOB BANK",
    "UOB BANK": "UOB BANK",
    "UNITED OVERSEAS BANK": "UOB BANK",
    "AFFIN BANK": "AFFIN BANK",
    "ALLIANCE BANK": "ALLIANCE BANK",
    "BSN": "BANK SIMPANAN NASIONAL",
    "BANK SIMPANAN NASIONAL": "BANK SIMPANAN NASIONAL",
}


def _normalize_bank_name(raw: str) -> str:
    stripped = "".join(ch for ch in raw if ch.isalnum() or ch.isspace())
    tokens = stripped.upper().split()
    core_tokens = [t for t in tokens if t not in _BANK_LEGAL_SUFFIX_WORDS]
    # Keep the unstripped form if every token was a "suffix" word (a name
    # that's somehow *only* "Berhad" isn't real, but don't reduce it to "").
    return " ".join(core_tokens) if core_tokens else " ".join(tokens)


def _canonical_bank_name(raw: str, threshold: float = 0.85) -> str:
    """Resolve a raw bank name to a canonical identity for consistency comparison.

    Tries the alias table first (exact match after stripping punctuation and
    case-folding) since it's predictable and auditable; falls back to a
    conservative fuzzy match against known canonical names only when no
    alias hits, so an unseen legal-name variant of a known bank ("Maybank
    Bhd" instead of "Maybank Berhad") doesn't read as a different bank. Two
    genuinely different banks are never folded together: an unrecognized
    name with no high-similarity match is returned normalized but otherwise
    untouched, so it still differs from every other bank's canonical form.
    """
    normalized = _normalize_bank_name(raw)
    alias_hit = _BANK_NAME_ALIASES.get(normalized)
    if alias_hit is not None:
        return alias_hit

    best_alias, best_score = best_fuzzy_match(normalized, _BANK_NAME_ALIASES)
    if best_alias is not None and best_score >= threshold:
        return _BANK_NAME_ALIASES[best_alias]
    return normalized


def check_bank_statement_bank_consistency(bank_names: List[Optional[str]]) -> Dict:
    """Check that every bank statement in the set is from the same bank.

    Use this for the bank_name of every bank_statement document in the
    bundle. Names are resolved to a canonical bank identity before
    comparing -- via a known-bank alias table first, then a conservative
    fuzzy match for unseen legal-name variants -- so "Maybank", "Maybank
    Berhad", and "Malayan Banking Berhad" are recognized as one bank rather
    than three. A null bank_name (extraction had no reliable source for it
    on that document) can't confirm consistency one way or the other, so it
    needs_review rather than fails; two or more distinct canonical bank
    identities is a confirmed fail.

    Args:
        bank_names: One entry per bank_statement document -- its bank_name,
            or null if not available for that document.
    """
    known_names = [name for name in bank_names if name is not None]
    distinct_raw = sorted(set(known_names))
    distinct_canonical = sorted({_canonical_bank_name(name) for name in known_names})
    unknown_count = len(bank_names) - len(known_names)

    if len(distinct_canonical) > 1:
        passed = False
        message = f"Bank statements come from {len(distinct_canonical)} different banks: {', '.join(distinct_canonical)}."
    elif unknown_count > 0:
        passed = None
        message = (
            "At least one bank statement has no confirmed bank name -- cannot "
            "confirm all statements are from the same bank."
        )
    elif distinct_canonical:
        passed = True
        message = f"All bank statements are from {distinct_canonical[0]}."
    else:
        passed = None
        message = "No bank name data available on any bank statement."

    return {
        "passed": passed,
        "message": message,
        "details": {
            "documents_checked": len(bank_names),
            "distinct_banks": distinct_canonical,
            "raw_bank_names": distinct_raw,
            "documents_with_unknown_bank": unknown_count,
        },
    }


def check_bank_statement_currency(
    currencies: List[Optional[str]],
    accepted_currency: str = BMMB_SME_POLICY_V1.accepted_bank_currency,
) -> Dict:
    """Check that every bank statement's currency matches the accepted currency.

    Use this for the currency of every bank_statement document in the
    bundle. A statement in a different currency isn't a confirmed
    compliance failure on its own -- it needs manual conversion (e.g. at the
    current Google rate) before its balances can be compared like-for-like,
    so a mismatch is a warning (needs_review), not a fail. A null currency
    (extraction had no reliable source for it on that document) is treated
    the same way: it can't be confirmed as the accepted currency either, so
    it needs review too.

    Args:
        currencies: One entry per bank_statement document -- its currency
            code (e.g. "MYR"), or null if not available for that document.
        accepted_currency: The currency code statements are expected to be
            in, e.g. "MYR".
    """
    normalized_accepted = accepted_currency.strip().upper()
    unknown_count = sum(1 for c in currencies if c is None)
    mismatched = sorted({c for c in currencies if c is not None and c.strip().upper() != normalized_accepted})

    if mismatched:
        passed = None
        message = (
            f"{len(mismatched)} bank statement currency/currencies ({', '.join(mismatched)}) "
            f"do not match the accepted currency ({normalized_accepted}) -- needs conversion "
            "and manual review."
        )
    elif unknown_count > 0:
        passed = None
        message = (
            "At least one bank statement has no confirmed currency -- cannot "
            f"confirm it matches the accepted currency ({normalized_accepted})."
        )
    else:
        passed = True
        message = f"All bank statements are in the accepted currency ({normalized_accepted})."

    return {
        "passed": passed,
        "message": message,
        "details": {
            "documents_checked": len(currencies),
            "accepted_currency": normalized_accepted,
            "mismatched_currencies": mismatched,
            "documents_with_unknown_currency": unknown_count,
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
