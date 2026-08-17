"""
Deterministic pre-check, run before the agent sees the message.

This is NOT vendored from v1 — it is the one place v2 deliberately diverges, so
it lives outside the drift guard. Two changes, both narrowing:

1. **No LLM stage.** v1 runs a second, model-based adversarial classifier after
   the regexes. v2 folds that judgement into the agent itself (see
   `policy/prompts/20_security.md`), which saves a round trip and lets the model
   weigh the whole conversation rather than one message in isolation.

2. **The ADV-06 company-name pattern is gone.** v1 flags
   `(status|application|data|details) (for|of) ... (sdn bhd|bhd|enterprise|trading)`
   as social engineering. It cannot tell whose company is named, so it refuses
   ordinary customers:

       "what's the status of my application for Maju Enterprise?"   -> refused
       "application for my enterprise financing"                    -> refused

   Nearly every Malaysian SME is a Sdn Bhd, Enterprise or Trading, so that is the
   normal phrasing for a core in-scope journey. The two patterns that name a
   third party explicitly ("another customer's", "someone else's") are kept —
   they encode the actual attack. Distinguishing "my company" from "their
   company" needs context, which is exactly what the agent has and a regex does
   not.

What stays deterministic is the obvious, unambiguous stuff: a regex is free,
runs before any token is spent, and cannot be talked out of firing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# (cat_id, pattern). Ported from services/chat/app/agents/guardrail/denylist.py.
# Order only decides which category is reported first; any hit refuses the turn.
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
    # NOTE: v1's third pattern here matched any company suffix near the words
    # "status/application/data/details". Removed — see the module docstring.
    ("ADV-06", re.compile(r"another\s+(customer|applicant|user|person)'?s?", re.I)),
    ("ADV-06", re.compile(r"someone\s+else'?s?\s+(application|data|account)", re.I)),
    # ADV-07 data exfiltration
    ("ADV-07", re.compile(r"(list|dump|show|export|give\s+me)\s+(me\s+)?(all|every)\b.{0,30}(application|record|customer|user|data)", re.I)),
    ("ADV-07", re.compile(r"all\s+(pending|the)\s+applications", re.I)),
    ("ADV-07", re.compile(r"\b(your|the)\s+(entire\s+)?database\b", re.I)),
    # ADV-08 obfuscation / encoding
    ("ADV-08", re.compile(r"\bbase\s?64\b", re.I)),
    ("ADV-08", re.compile(r"decode\s+(this|the\s+following)", re.I)),
    # A long unbroken alphanumeric run — base64-ish payload. Requires mixed case
    # AND a digit so ordinary long words and reference numbers don't trip it.
    ("ADV-08", re.compile(r"(?=[A-Za-z0-9+/]{24,})(?=[^A-Z]*[A-Z])(?=[^a-z]*[a-z])(?=[^0-9]*[0-9])[A-Za-z0-9+/]{24,}={0,2}")),
]

# ADV-04/05 (gaming eligibility) get R7, which still offers the general criteria.
# Everything else gets the flat R6. Same mapping as v1's intents.yaml.
_R7 = {"ADV-04", "ADV-05"}


@dataclass(frozen=True)
class Verdict:
    flagged: bool
    category: Optional[str] = None
    response_ref: Optional[str] = None


CLEAN = Verdict(flagged=False)


def screen(message: str) -> Verdict:
    """Refuse-or-continue on the CURRENT message only.

    Never scans history: a transcript is attacker-controllable, so scanning it
    would let a poisoned earlier turn flag or unflag the present one.
    """
    if not message:
        return CLEAN
    for cat_id, pattern in _PATTERNS:
        if pattern.search(message):
            return Verdict(True, cat_id, "R7" if cat_id in _R7 else "R6")
    return CLEAN