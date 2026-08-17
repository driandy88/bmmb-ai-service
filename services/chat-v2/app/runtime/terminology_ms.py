"""
Malay-language terminology guard.

The vendored `utils/terminology.py` only knows English — it rewrites loan and
interest, and passes `pinjaman` and `kadar faedah` straight through. In v1 that
gap rarely bites because most of what v1 sends is canned English wording from
responses.yaml. v2 composes Malay prose freely, so the same gap becomes a live
compliance risk.

v1 has the same hole and should get the same fix, but terminology.py is vendored
byte-identical and the drift guard exists to stop exactly that kind of local
edit. So this runs as a SECOND pass, after the English one, and leaves the
vendored file alone.

## Why two tiers

Malay is not a clean find-and-replace, because the riba vocabulary overlaps with
ordinary words:

  * `pinjaman` means loan and nothing else       -> safe to rewrite
  * `kadar faedah` / `kadar bunga` = interest rate -> safe to rewrite
  * bare `faedah` also means "benefit"           -> "faedah program ini" is
    "the benefits of this programme", a perfectly good sentence that must not
    become "the profits of this programme"
  * bare `bunga` also means "flower"

So unambiguous collocations are rewritten, and bare ambiguous terms are FLAGGED
for review rather than silently mangled. A flag is not a pass — it means a human
should look at that turn and probably tighten a prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Rewritten. Each of these is riba vocabulary with no innocent reading in a
# financing context. Order matters: longest collocation first.
_REWRITE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bkadar[- ]faedah\b", re.I), "kadar keuntungan"),
    (re.compile(r"\bkadar[- ]bunga\b", re.I), "kadar keuntungan"),
    (re.compile(r"\bfaedah\s+bank\b", re.I), "keuntungan bank"),
    (re.compile(r"\bpinjaman\b", re.I), "pembiayaan"),
    (re.compile(r"\bmeminjamkan\b", re.I), "membiayai"),
    (re.compile(r"\bpeminjam\b", re.I), "pelanggan pembiayaan"),
]

# Flagged only — these have legitimate non-financial meanings, so rewriting them
# would corrupt correct sentences. Surfaced in the audit for review.
_FLAG_ONLY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bfaedah\b", re.I), "faedah (may mean 'interest' — check; 'benefit' is fine)"),
    (re.compile(r"\bbunga\b", re.I), "bunga (may mean 'interest' — check; 'flower' is fine)"),
    (re.compile(r"\briba\b", re.I), "riba (fine when explaining what is prohibited; never as a product feature)"),
]


def _match_case(replacement: str, original: str) -> str:
    """Preserve the original's casing shape, same convention as the English lint."""
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


@dataclass
class MsLintResult:
    text: str
    rewritten: list[str]
    flagged: list[str]

    @property
    def clean(self) -> bool:
        return not self.rewritten and not self.flagged


def lint_ms(text: str) -> MsLintResult:
    """Rewrite unambiguous Malay riba terms; flag the ambiguous ones."""
    if not text:
        return MsLintResult(text, [], [])

    rewritten: list[str] = []
    out = text
    for pattern, replacement in _REWRITE:
        def _sub(m: re.Match) -> str:
            rewritten.append(m.group(0))
            return _match_case(replacement, m.group(0))
        out = pattern.sub(_sub, out)

    # Flag against the REWRITTEN text so a term we already fixed (kadar faedah ->
    # kadar keuntungan) does not also get reported as an unresolved ambiguity.
    flagged = [note for pattern, note in _FLAG_ONLY if pattern.search(out)]
    return MsLintResult(out, rewritten, flagged)