"""
Maximum working-capital limit — a computed OUTPUT, deterministic like everything
else in this package (rules.py). The model never computes this; it only relays
what this function returns.

Formula and default (revenue x 30%) mirror the Node backend's
PolicyThresholds.financing.wclRevenuePct (bmmb-sme-financing-platform/frontend/
src/utils/financingRatios.ts calcWorkingCapitalLimit) — duplicated rather than
wired cross-service, since this chat service has no call path to that policy
endpoint today.
"""
from __future__ import annotations

from typing import Optional


def compute_working_capital_limit(revenue: Optional[float], pct: float) -> Optional[float]:
    if revenue is None:
        return None
    return round(float(revenue) * pct, -3)  # nearest RM 1,000
