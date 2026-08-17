"""
Assembles the agent's system prompt from the markdown policy files.

Two ideas here.

1. **Policy lives in markdown, not Python.** A compliance reviewer or a product
   owner can read `prompts/*.md` and `agents/orchestrator.md` and see exactly
   what the assistant has been told. Nothing behavioural hides in code.

2. **The taxonomy is injected, not duplicated.** `intents.yaml` stays the single
   source of truth for scope, exactly as in v1 — the markdown carries
   `{in_scope}` / `{out_of_scope}` / `{adversarial}` placeholders and this module
   renders the live rows into them. Add an intent to the YAML and v2 knows about
   it on next boot, with no prompt edit.

The HTML comments at the top of each .md are notes for whoever maintains the
file. They are stripped before the model ever sees them.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.config.loader import Taxonomy, load_config

_POLICY_DIR = Path(__file__).parent
_PROMPT_DIR = _POLICY_DIR / "prompts"
_AGENT_DIR = _POLICY_DIR / "agents"

# Order matters: identity first (who you are), then scope, then the security
# rules that override scope, then how to ground answers, then how to hold a
# conversation. Same precedence intent as v1's routing ladder, expressed as
# reading order rather than an if-chain.
_PROMPT_ORDER = [
    "00_identity.md",
    "10_scope.md",
    "20_security.md",
    "30_grounding.md",
    "40_conversation.md",
]

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_notes(text: str) -> str:
    """Drop maintainer comments; they are provenance for humans, not instructions."""
    return _COMMENT_RE.sub("", text).strip()


def _bullet(rows) -> str:
    return "\n".join(f"- **{r.category}** — {r.definition}" for r in rows)


def _bullet_with_ref(rows) -> str:
    return "\n".join(
        f"- **{r.category}** — {r.definition} → `{r.response_ref}`" for r in rows
    )


def _render_taxonomy(tax: Taxonomy) -> dict[str, str]:
    """Live taxonomy rows -> the placeholders the markdown expects.

    `ambiguous` rows are folded in rather than given their own section. In v1
    they are a routing category ("we are not sure, so clarify"); in v2 that is
    just a judgement the agent makes, so the rows that carry real scope meaning
    (AMB-05 Shariah-boundary) belong with in-scope, and the rest are covered by
    the "Judgement calls" section of 10_scope.md.
    """
    in_scope = tax.of_type("in_scope") + [
        r for r in tax.of_type("ambiguous") if r.is_route
    ]
    out_of_scope = tax.of_type("out_of_scope")
    adversarial = tax.of_type("adversarial")
    return {
        "in_scope": _bullet(in_scope),
        "out_of_scope": _bullet_with_ref(out_of_scope),
        "adversarial": _bullet_with_ref(adversarial),
        "taxonomy_note": (
            f"         rendered from {len(tax.rows)} rows at load time — "
            f"edit intents.yaml, not this file."
        ),
    }


@lru_cache(maxsize=1)
def system_prompt() -> str:
    """The assembled system prompt. Cached — the policy files are read once at
    startup, the same way v1 loads its YAML once."""
    tax = load_config().taxonomy
    fill = _render_taxonomy(tax)

    parts: list[str] = []
    for name in _PROMPT_ORDER:
        raw = _strip_notes((_PROMPT_DIR / name).read_text(encoding="utf-8"))
        for key, value in fill.items():
            raw = raw.replace("{" + key + "}", value)
        parts.append(raw)

    return "\n\n---\n\n".join(parts)


@lru_cache(maxsize=1)
def agent_card() -> str:
    """The orchestrator's own definition. Not sent to the model — it documents
    the agent for humans and is surfaced at /v2/policy so what is deployed can be
    inspected without reading the image."""
    return _strip_notes((_AGENT_DIR / "orchestrator.md").read_text(encoding="utf-8"))