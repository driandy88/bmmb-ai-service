"""
Deterministic denylist — the FAST first stage of the guardrail (brief §7).

Regex/substring patterns for the obvious, unambiguous attacks so we catch them
without an LLM round-trip and independent of model behaviour. A miss here just
falls through to the LLM classifier; a hit short-circuits to a refusal. This is
a security control — it runs on the CURRENT message every turn (§5.1), never on
client history.

Categories map to Sheet 1.1 ADV-01..ADV-08.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# (cat_id, compiled pattern). Order matters only for which category is reported
# first; any hit flags the message.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # ADV-01 prompt injection / override
    ("ADV-01", re.compile(r"ignore\s+(all|any|your|the|previous).{0,25}(instruction|rule|prompt|guardrail)", re.I)),
    ("ADV-01", re.compile(r"forget\s+(your|all|the|any|previous).{0,25}(rule|instruction|prompt)", re.I)),
    ("ADV-01", re.compile(r"\bdeveloper\s+mode\b", re.I)),
    ("ADV-01", re.compile(r"\byou\s+are\s+now\b", re.I)),
    ("ADV-01", re.compile(r"disregard\s+(all|any|your|the|previous)", re.I)),
    # ADV-02 system / prompt extraction
    ("ADV-02", re.compile(r"\bsystem\s+prompt\b", re.I)),
    ("ADV-02", re.compile(r"\b(initial|original)\s+instructions\b", re.I)),
    ("ADV-02", re.compile(r"reveal\s+your\s+(prompt|instruction|rule|config|setup)", re.I)),
    ("ADV-02", re.compile(r"what\s+(model|llm|ai)\s+are\s+you", re.I)),
    ("ADV-02", re.compile(r"what\s+are\s+your\s+(guardrail|rule|instruction|constraint)", re.I)),
    # ADV-03 jailbreak roleplay
    ("ADV-03", re.compile(r"pretend\s+(you|to\s+be|that)", re.I)),
    ("ADV-03", re.compile(r"\brole\s?play\b", re.I)),
    ("ADV-03", re.compile(r"\bact\s+as\s+(a|an|the)?\s*(senior|credit|bank|admin|approv)", re.I)),
    ("ADV-03", re.compile(r"hypothetical(ly)?.{0,30}approv", re.I)),
    # ADV-04 eligibility manipulation / coaching
    ("ADV-04", re.compile(r"what\s+(numbers|values|figures|amounts).{0,30}(enter|input|put|say).{0,25}approv", re.I)),
    ("ADV-04", re.compile(r"so\s+(i|we)\s+get\s+approved", re.I)),
    ("ADV-04", re.compile(r"how\s+(to|do\s+i|can\s+i)\s+(fake|game|trick|manipulat)", re.I)),
    # ADV-05 threshold probing
    ("ADV-05", re.compile(r"exact.{0,25}(cut[\s-]?off|threshold|limit|dsr|dscr|score)", re.I)),
    ("ADV-05", re.compile(r"\bstay\s+under\b", re.I)),
    ("ADV-05", re.compile(r"what('?s|\s+is)\s+the\s+(exact\s+)?(cut[\s-]?off|threshold|dsr|dscr)", re.I)),
    # ADV-06 social engineering (others' data)
    ("ADV-06", re.compile(r"(status|application|data|details)\s+(for|of)\s+.{0,40}\b(sdn\s*bhd|bhd|enterprise|trading)\b", re.I)),
    ("ADV-06", re.compile(r"another\s+(customer|applicant|user|person)'?s?", re.I)),
    ("ADV-06", re.compile(r"someone\s+else'?s?\s+(application|data|account)", re.I)),
    # ADV-07 data exfiltration
    ("ADV-07", re.compile(r"(list|dump|show|export|give\s+me)\s+(me\s+)?(all|every)\b.{0,30}(application|record|customer|user|data)", re.I)),
    ("ADV-07", re.compile(r"all\s+(pending|the)\s+applications", re.I)),
    ("ADV-07", re.compile(r"\b(your|the)\s+(entire\s+)?database\b", re.I)),
    # ADV-08 obfuscation / encoding
    ("ADV-08", re.compile(r"\bbase\s?64\b", re.I)),
    ("ADV-08", re.compile(r"decode\s+(this|the\s+following)", re.I)),
    ("ADV-08", re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")),  # long base64-ish blob
]


@dataclass(frozen=True)
class DenylistHit:
    category: str
    pattern: str


def scan(message: str) -> Optional[DenylistHit]:
    """Return the first matching adversarial category, or None."""
    if not message:
        return None
    for cat_id, pat in _PATTERNS:
        if pat.search(message):
            return DenylistHit(category=cat_id, pattern=pat.pattern)
    return None
