"""Deterministic bank-statement aggregation: daily transactions -> monthly -> yearly.

No LLM, no network — pure arithmetic over already-extracted transaction rows,
so the result is exact, reproducible and unit-testable. This is the deliberate
split from services.extraction: the LLM transcribes what is printed (daily
rows), and this layer does the maths (monthly sums, yearly averages) that an
LLM would do less reliably.

Ported from the manual_extraction_test.ipynb §6 prototype and generalised to
accept several statement documents at once, pooled per (bank, account).
"""
from collections import defaultdict
from datetime import date, datetime
from statistics import mean
from typing import Optional

# Extraction should emit ISO YYYY-MM-DD, but real statements print many formats and the
# LLM occasionally passes one through. Parse defensively so a stray format degrades to a
# warning instead of crashing the rollup. Order matters: try ISO first.
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y")


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    v = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _continuity_warnings(source_document: str, rows: list[dict]) -> list[str]:
    """Each running balance should equal the previous one minus debit plus credit,
    in the order the rows were printed. A mismatch means a transaction row was
    probably missed or misread by extraction — surface it rather than silently
    averaging a wrong figure. Continuity is per statement (the balance resets per
    document), so this is called once per source document."""
    warnings: list[str] = []
    prev: Optional[float] = None
    for i, r in enumerate(rows):
        bal = r.get("balance")
        deb = r.get("debit") or 0
        cred = r.get("credit") or 0
        if prev is not None and bal is not None:
            expected = round(prev - deb + cred, 2)
            if abs(expected - bal) > 0.01:
                warnings.append(
                    f"{source_document} row {i} ({r.get('date')}): balance {bal} "
                    f"!= expected {expected} (prev {prev} - debit {deb} + credit {cred}); "
                    f"a transaction row may be missing or misread"
                )
        if bal is not None:
            prev = bal
    return warnings


def _month_end_balance(rows: list[tuple[date, dict]]) -> Optional[float]:
    """Balance of the latest-dated row in the month (ties broken by input order —
    the last occurrence wins). `rows` are (parsed_date, row) in input order."""
    best_dt, best_row = None, None
    for dt, r in rows:
        if r.get("balance") is None:
            continue
        if best_dt is None or dt >= best_dt:
            best_dt, best_row = dt, r
    return best_row.get("balance") if best_row else None


def aggregate_bank(documents: list[dict]) -> dict:
    """`documents`: raw per-statement extractions, each
    {source_document, bank_name, account_number_masked, transactions: [...]}.

    Returns {"accounts": [...]} — one entry per (bank_name, account_number_masked),
    each with monthly totals, yearly averages, the source documents it drew from,
    and any balance-continuity warnings. Deterministic and order-stable.
    """
    tx_by_account: dict = defaultdict(list)
    docs_by_account: dict = defaultdict(list)
    warnings_by_account: dict = defaultdict(list)

    for doc in documents:
        key = (doc.get("bank_name"), doc.get("account_number_masked"))
        src = doc.get("source_document") or "(unknown)"
        rows = doc.get("transactions") or []
        docs_by_account[key].append(src)
        warnings_by_account[key] += _continuity_warnings(src, rows)
        tx_by_account[key] += rows

    accounts = []
    for key, rows in tx_by_account.items():
        bank_name, account_number_masked = key

        # daily -> monthly. Dates are parsed defensively; an unparseable date is
        # warned about and excluded from the rollup rather than crashing it.
        by_month: dict = defaultdict(list)
        for r in rows:
            dt = _parse_date(r.get("date"))
            if dt is None:
                if r.get("date"):
                    warnings_by_account[key].append(
                        f"unparseable transaction date {r.get('date')!r}; row excluded from rollup"
                    )
                continue
            by_month[f"{dt.year:04d}-{dt.month:02d}"].append((dt, r))
        monthly = []
        for ym in sorted(by_month):
            mrows = by_month[ym]
            monthly.append({
                "month": ym,
                "txn_count": len(mrows),
                "monthly_deposit": round(sum(r["credit"] for _, r in mrows if r.get("credit")), 2),
                "monthly_withdrawal": round(sum(r["debit"] for _, r in mrows if r.get("debit")), 2),
                "monthly_end_balance": _month_end_balance(mrows),
            })

        # monthly -> yearly average
        by_year: dict = defaultdict(list)
        for m in monthly:
            by_year[m["month"][:4]].append(m)
        yearly = []
        for y in sorted(by_year):
            ms = by_year[y]
            bals = [m["monthly_end_balance"] for m in ms if m["monthly_end_balance"] is not None]
            yearly.append({
                "year": int(y),
                "months_covered": len(ms),
                "avg_monthly_deposit": round(mean(m["monthly_deposit"] for m in ms), 2),
                "avg_monthly_withdrawal": round(mean(m["monthly_withdrawal"] for m in ms), 2),
                "avg_monthly_end_balance": round(mean(bals), 2) if bals else None,
            })

        accounts.append({
            "bank_name": bank_name,
            "account_number_masked": account_number_masked,
            "source_documents": docs_by_account[key],
            "monthly": monthly,
            "yearly": yearly,
            "integrity_warnings": warnings_by_account[key],
        })

    accounts.sort(key=lambda a: (str(a["bank_name"]), str(a["account_number_masked"])))
    return {"accounts": accounts}
