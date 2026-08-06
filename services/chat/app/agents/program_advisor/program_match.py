"""
Deterministic programme-name detection.

The LLM intent classifier is blind to BMMB's programme acronyms (GGSM, MIHP,
TERAJU, …), so a bare "what is GGSM" / "what is MIHP" is a coin-flip — sometimes
INS-02 (programme query → grounded answer), sometimes filed as off-topic
(OOS-05/06/07 → the "not my department" redirect). It's also history-primed.

This is the deterministic rescue: any message that NAMES a known programme is a
programme query. `classify_node` uses it to override an off-topic / social /
ambiguous classification to INS-02 (adversarial still wins — the guardrail runs
first). The programme set is config-driven (products.yaml), so it tracks the
recommender's catalogue, plus a couple of index-only / alias tokens.
"""
from __future__ import annotations

import re
from functools import lru_cache

from app.config.loader import load_config

# Programmes that live in the RAG index but not in products.yaml's quantum table,
# plus the shared "Madani" family word — so "what is Madani 3" also routes in.
_EXTRA_ALIASES = {"PROUD", "MADANI"}


@lru_cache(maxsize=1)
def _pattern() -> re.Pattern:
    codes = {
        (p.get("program") or "").strip().upper()
        for p in load_config().products.get("quantum", [])
        if (p.get("program") or "").strip()
    } | _EXTRA_ALIASES
    # Longest-first so e.g. MIHP is tried before MHP. Each base acronym may carry
    # a naming-drift suffix: GGSM3, MHP-I, MIHP-i, GGSM 4 → still the same programme.
    alts = "|".join(sorted((re.escape(c) for c in codes), key=len, reverse=True))
    return re.compile(rf"\b(?:{alts})(?:[-\s]?i|[-\s]?\d+)?\b", re.IGNORECASE)


def mentions_program(message: str) -> bool:
    """True when the message names a known BMMB SME-financing programme."""
    return bool(_pattern().search(message or ""))
