"""
Terminology lint (brief §2.1, §12) — enforce Islamic-finance wording on the
bot's OWN outgoing replies.

The bot must never emit "loan" or "interest (rate)". This runs as a post-check
on every composed reply and rewrites forbidden terms to their compliant forms,
preserving capitalisation. It returns the cleaned text plus the violations it
caught (logged as a quality signal — a violation means a prompt needs tightening).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters: multi-word rules before single-word (interest rate -> profit
# rate before interest -> profit).
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\binterest[- ]rates\b", re.I), "profit rates"),
    (re.compile(r"\binterest[- ]rate\b", re.I), "profit rate"),
    (re.compile(r"\binterests\b", re.I), "profits"),
    (re.compile(r"\binterest\b", re.I), "profit"),
    (re.compile(r"\bloans\b", re.I), "financing"),
    (re.compile(r"\bloan\b", re.I), "financing"),
]


@dataclass
class LintResult:
    text: str
    violations: list[str]

    @property
    def clean(self) -> bool:
        return not self.violations


def _match_case(replacement: str, original: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def lint(text: str) -> LintResult:
    violations: list[str] = []
    out = text or ""
    for pattern, replacement in _RULES:
        def _sub(m: re.Match) -> str:
            violations.append(m.group(0))
            return _match_case(replacement, m.group(0))
        out = pattern.sub(_sub, out)
    return LintResult(text=out, violations=violations)


def has_violations(text: str) -> bool:
    return not lint(text).clean
