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


_QUOTES = set("\"'“”‘’")
# A qualifier that makes "loan"/"interest" refer to a NON-Islamic product the bot is contrasting
# with, not to our own offering — so it's compliant to keep it ("a conventional loan").
_CONTRAST_BEFORE = re.compile(r"(?:conventional|traditional|ordinary|non[- ]?islamic)\W+$", re.I)


def _deliberate(text: str, start: int, end: int) -> bool:
    """The forbidden term is used LEGITIMATELY here — a deliberate contrast, not the bot mislabelling
    our own product — so keep it rather than rewrite it into a self-contradiction ("financing rather
    than conventional financing"). Signals: the term is in quotes (the bot is naming it), or a
    'conventional / traditional' qualifier precedes it. Compliance still catches a bare slip."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before in _QUOTES and after in _QUOTES:
        return True
    return bool(_CONTRAST_BEFORE.search(text[max(0, start - 20):start]))


def lint(text: str) -> LintResult:
    violations: list[str] = []
    out = text or ""
    for pattern, replacement in _RULES:
        def _sub(m: re.Match) -> str:
            if _deliberate(m.string, m.start(), m.end()):
                return m.group(0)                       # kept on purpose — not a violation
            violations.append(m.group(0))
            return _match_case(replacement, m.group(0))
        out = pattern.sub(_sub, out)
    return LintResult(text=out, violations=violations)


def has_violations(text: str) -> bool:
    return not lint(text).clean
