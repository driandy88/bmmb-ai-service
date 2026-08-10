"""
Loads the BMMB-owned YAML config (taxonomy, canned responses, eligibility
rules, products, sales directory) into typed objects the rest of the service
reads from. This is the ONLY module that knows the on-disk file layout.

Editability contract (brief §4.2): the taxonomy and canned wording are pure
data here. Adding/editing/removing an intent or re-pointing a `response_ref`
is a one-YAML-entry change with zero Python edits — the classifier prompt and
the router both read `Taxonomy`/`Responses` built here. `reload()` clears the
cache so the notebook can re-score after an edit.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from .settings import Settings, get_settings

_CONFIG_DIR = Path(__file__).resolve().parent


def _read_yaml(name: str) -> Any:
    with open(_CONFIG_DIR / name, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ── Taxonomy (intents.yaml) ──────────────────────────────────────────────────

@dataclass(frozen=True)
class IntentRow:
    cat_id: str
    category: str
    definition: str
    response_ref: str
    type: str                       # in_scope | out_of_scope | adversarial | ambiguous
    status: Optional[str] = None    # e.g. "tbd" -> safe default until wording provided
    # A specific, recognisable off-topic topic (fixed deposit, personal loan, a competitor,
    # investment advice) rather than vague "I don't recognise this" chit-chat. When the classifier
    # confidently picks one of these, a programme name in the message is incidental — the
    # programme-name rescue (nodes.classify_node) yields instead of hijacking it into a query.
    specific_topic: bool = False

    @property
    def is_route(self) -> bool:
        return self.response_ref.upper().startswith("ROUTE-")


@dataclass
class Taxonomy:
    rows: list[IntentRow]
    by_id: dict[str, IntentRow] = field(default_factory=dict)

    def get(self, cat_id: Optional[str]) -> Optional[IntentRow]:
        return self.by_id.get(cat_id) if cat_id else None

    def ids(self) -> list[str]:
        return [r.cat_id for r in self.rows]

    def of_type(self, t: str) -> list[IntentRow]:
        return [r for r in self.rows if r.type == t]


def _build_taxonomy(raw: list[dict]) -> Taxonomy:
    rows = [
        IntentRow(
            cat_id=str(r["cat_id"]).strip(),
            category=str(r["category"]).strip(),
            definition=str(r.get("definition", "")).strip(),
            response_ref=str(r["response_ref"]).strip(),
            type=str(r["type"]).strip(),
            status=(str(r["status"]).strip() if r.get("status") else None),
            specific_topic=bool(r.get("specific_topic", False)),
        )
        for r in raw
    ]
    return Taxonomy(rows=rows, by_id={r.cat_id: r for r in rows})


# ── Canned responses (responses.yaml) ────────────────────────────────────────

@dataclass(frozen=True)
class ResponseStrategy:
    ref: str
    strategy: str
    applies_to: str
    variants: tuple[str, ...]
    notes: Optional[str] = None
    terminal: bool = True          # R8 (clarification) is non-terminal

    def wording(self, **fmt: Any) -> str:
        """Pick a RANDOM approved variant so canned replies vary between turns
        instead of always repeating the first (business owns the list in YAML —
        every variant is pre-approved, so any is safe to send; refusals R6/R7 are
        deliberately generic, never attack-tailored). `.format(**fmt)` fills
        placeholders like {financing_product}; missing keys stay literal."""
        text = random.choice(self.variants) if self.variants else ""
        try:
            return text.format(**fmt)
        except (KeyError, IndexError):
            return text


@dataclass
class Responses:
    by_ref: dict[str, ResponseStrategy]

    def get(self, ref: Optional[str]) -> Optional[ResponseStrategy]:
        if not ref:
            return None
        return self.by_ref.get(ref.strip().upper())

    def wording(self, ref: str, **fmt: Any) -> str:
        strat = self.get(ref)
        return strat.wording(**fmt) if strat else ""


def _build_responses(raw: list[dict]) -> Responses:
    by_ref: dict[str, ResponseStrategy] = {}
    for r in raw:
        ref = str(r["ref"]).strip().upper()
        by_ref[ref] = ResponseStrategy(
            ref=ref,
            strategy=str(r.get("strategy", "")).strip(),
            applies_to=str(r.get("applies_to", "")).strip(),
            variants=tuple(str(v) for v in r.get("variants", [])),
            notes=(str(r["notes"]).strip() if r.get("notes") else None),
            terminal=bool(r.get("terminal", True)),
        )
    return Responses(by_ref=by_ref)


# ── Aggregate config ─────────────────────────────────────────────────────────

@dataclass
class AppConfig:
    taxonomy: Taxonomy
    responses: Responses
    products: dict            # products.yaml (quantum table + funnel)
    eligibility: dict         # eligibility_rules.yaml (rule_version + tier-1/tier-2)
    sales: dict               # sales_directory.yaml (triggers, geo, directory, handoff msgs)
    settings: Settings

    @property
    def rule_version(self) -> str:
        return str(self.eligibility.get("rule_version", "eligibility_v1"))


@lru_cache(maxsize=1)
def load_config() -> AppConfig:
    return AppConfig(
        taxonomy=_build_taxonomy(_read_yaml("intents.yaml")),
        responses=_build_responses(_read_yaml("responses.yaml")),
        products=_read_yaml("products.yaml"),
        eligibility=_read_yaml("eligibility_rules.yaml"),
        sales=_read_yaml("sales_directory.yaml"),
        settings=get_settings(),
    )


def reload_config() -> AppConfig:
    """Clear the cache and reload — used by the notebook after editing a YAML
    so taxonomy/threshold changes are testable immediately (brief §4.2, §10C)."""
    load_config.cache_clear()
    return load_config()
