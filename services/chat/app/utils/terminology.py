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
# The forbidden term appears here LEGITIMATELY — the bot is NAMING or CONTRASTING the conventional
# concept (the customer's own word, or a conventional-bank product it sets us apart from), not
# mislabelling our own offering. A contrast / naming / negation cue just before the term is the
# tell. This is what lets the terminology REFRAME read naturally without the lint wrecking it:
#   "financing, not a conventional loan"  ·  "we don't charge interest — we share a profit rate".
_KEEP_CUES = re.compile(
    r"\b(?:conventional|traditional|ordinary|non[- ]?islamic|so[- ]?called|rather|instead|unlike|"
    r"versus|vs|not|never|without|don['’]?t|doesn['’]?t|won['’]?t|isn['’]?t|aren['’]?t|can['’]?t|"
    r"term|word)\b", re.I)


def _deliberate(text: str, start: int, end: int) -> bool:
    """The forbidden term is used LEGITIMATELY here — a deliberate contrast / naming, not the bot
    mislabelling our own product — so keep it rather than rewrite it into a self-contradiction
    ("financing rather than conventional financing", "we don't charge profit"). Signals: a
    contrast/naming/negation cue precedes it, or it's wrapped in quotes (the bot is quoting the word).
    Compliance still catches a bare slip (no cue, no quotes)."""
    if _KEEP_CUES.search(text[max(0, start - 28):start]):
        return True
    before = text[start - 1] if start > 0 else ""
    tail = text[end:end + 3]  # tolerate trailing punctuation inside the closing quote ("loan,")
    return before in _QUOTES and any(q in tail for q in _QUOTES)


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
