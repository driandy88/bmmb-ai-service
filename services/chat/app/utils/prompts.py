"""
Load versioned prompt files from app/prompts/*.md at runtime (brief §8 — no
large prompts inlined in .py). The leading HTML header comment (purpose/model/
output/excel metadata for human reviewers) is stripped before the text is sent
to the model.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_HEADER = re.compile(r"^\s*<!--.*?-->\s*", re.DOTALL)


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Return the body of prompts/<name>.md with its metadata header stripped."""
    text = (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    return _HEADER.sub("", text).strip()


def response_style() -> str:
    """The global style/safety preamble prepended to every generation prompt."""
    return load_prompt("response_style")


def _optional_prompt(name: str) -> str:
    """load_prompt, but an absent file is not an error — returns "". Lets a swappable
    preamble (e.g. behaviour.md) be added or removed without touching code."""
    try:
        return load_prompt(name)
    except FileNotFoundError:
        return ""


def system_prompt(name: str) -> str:
    """response_style (safety) + behaviour (the swappable brand voice) + the named task prompt,
    joined as one system instruction. Editing prompts/behaviour.md re-skins the assistant's
    demeanour everywhere; deleting it degrades gracefully to the safety preamble alone."""
    parts = [response_style(), _optional_prompt("behaviour"), load_prompt(name)]
    return "\n\n---\n\n".join(p for p in parts if p)


def render(template: str, **vars: object) -> str:
    """Fill only the named `{placeholder}` tokens by literal replacement — NOT
    str.format(), so prompt bodies can contain literal JSON braces (e.g. few-shot
    `{"primary": ...}` examples) without breaking. Unknown braces are left as-is."""
    out = template
    for key, value in vars.items():
        out = out.replace("{" + key + "}", str(value))
    return out

