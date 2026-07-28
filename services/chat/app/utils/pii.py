"""
PII redaction (brief §2.5, §12) — scrub sensitive data BEFORE it reaches any
log or telemetry sink. Raw prompts containing customer data must never be logged.

Redacts Malaysian IC/NRIC numbers, emails, phone numbers, and RM money amounts
(financials). Names are NOT reliably detectable without NER — a known limitation;
the mitigation is that the audit trail never stores free-text customer prose in
the first place (it logs rule OUTCOMES, not raw figures — see agents/eligibility).
"""
from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # NRIC: 900101-01-1234 or 12 straight digits.
    (re.compile(r"\b\d{6}-\d{2}-\d{4}\b"), "[IC_REDACTED]"),
    (re.compile(r"\b\d{12}\b"), "[IC_REDACTED]"),
    # Email.
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL_REDACTED]"),
    # Malaysian phone: 01x-xxx xxxx / +60... / 0x-xxxxxxx.
    (re.compile(r"\b(?:\+?60|0)\d[\d\s-]{6,10}\d\b"), "[PHONE_REDACTED]"),
    # RM money amounts (financials).
    (re.compile(r"\bRM\s?\d[\d,]*(?:\.\d+)?\b", re.I), "[AMOUNT_REDACTED]"),
    # Comma-grouped large numbers (e.g. 1,000,000) even without RM.
    (re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"), "[AMOUNT_REDACTED]"),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_obj(obj: Any) -> Any:
    """Recursively redact string values in dicts/lists (for structured logs)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj
