"""
Map document-extraction output → Tier-1 eligibility slots (brief §4.4 / §7).

Pure, deterministic, config-driven (config/document_slot_map.yaml). Reads ONLY
the Tier-1 figures named in that config; Tier-2 fields present in the same
document (ebitda_net_profit, pbt, advances_due_to_director, …) are never
touched, so document-fed eligibility stays inside the Tier-1 boundary.

`extracted_data` is the `data.extracted_data` object from the extraction
service's /extract response. Repeating groups (multi-year AFS, multi-month bank
statements) arrive as an array; the array is located by CONTENT (the first list
of dicts containing the target field) so the mapper does not depend on the Cloud
SQL template's row_group key name, and the most-recent row is chosen via
`recency_key`.
"""
from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

_CONFIG_PATH = Path(__file__).resolve().parents[1].parent / "config" / "document_slot_map.yaml"


@lru_cache(maxsize=1)
def load_slot_map() -> dict:
    with _CONFIG_PATH.open() as f:
        return yaml.safe_load(f) or {}


def _to_number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d[\d,]*(?:\.\d+)?", v)
        if m:
            return float(m.group(0).replace(",", ""))
    return None


def _year_of(v: Any) -> Optional[int]:
    if v is None:
        return None
    m = re.search(r"(19|20)\d{2}", str(v))
    return int(m.group(0)) if m else None


# Month tokens (English + Bahasa Malaysia) so same-year statements order by month.
_MONTHS = [
    (1, ("jan",)), (2, ("feb",)), (3, ("mar", "mac")), (4, ("apr",)),
    (5, ("may", "mei")), (6, ("june", "jun")), (7, ("july", "julai", "jul")),
    (8, ("aug", "ogos")), (9, ("sep",)), (10, ("oct", "okt")), (11, ("nov",)), (12, ("dec", "dis")),
]


def _month_of(v: Any) -> int:
    if v is None:
        return 0
    s = str(v).lower()
    for num, aliases in _MONTHS:
        if any(a in s for a in aliases):
            return num
    m = re.search(r"\b(\d{1,2})[/-]\d{4}\b|\b\d{4}[/-](\d{1,2})\b", s)  # MM/YYYY or YYYY-MM
    if m:
        mm = int(m.group(1) or m.group(2))
        if 1 <= mm <= 12:
            return mm
    return 0


def _period_key(v: Any) -> tuple[int, int]:
    """(year, month) for recency comparison; -1/0 when unparseable."""
    return (_year_of(v) if _year_of(v) is not None else -1, _month_of(v))


def _date_to_years(v: Any, *, today: date) -> Optional[float]:
    y = _year_of(v)
    if y is None:
        return None
    return float(max(0, today.year - y))


def _find_group_array(extracted: Any, field: str) -> Optional[list]:
    """Locate the repeating-group array by content: the first list of dicts whose
    elements contain `field`. Robust to the DB row_group key name."""
    if not isinstance(extracted, dict):
        return None
    for k, v in extracted.items():
        if k == "_locations":
            continue
        if isinstance(v, list) and v and all(isinstance(e, dict) for e in v):
            if any(field in e for e in v):
                return v
    return None


def _pick_recent(rows: list, recency_key: Optional[str]) -> Optional[dict]:
    """Most-recent row by (year, month) parsed out of `recency_key`; ties and
    unparseable periods fall back to the later row (documents arrive in order)."""
    if not rows:
        return None
    if not recency_key:
        return rows[-1]
    best, best_k = None, None
    for r in rows:
        k = _period_key(r.get(recency_key))
        if best is None or k >= best_k:   # >= → later row wins ties
            best, best_k = r, k
    return best


def map_document_to_slots(
    template_id: str,
    extracted_data: dict,
    *,
    slot_map: Optional[dict] = None,
    today: Optional[date] = None,
) -> dict:
    """Return the Tier-1 slots this template can fill (only non-null values)."""
    slot_map = slot_map or load_slot_map()
    today = today or date.today()
    out: dict[str, float] = {}

    for slot, spec in (slot_map.get("slots") or {}).items():
        if spec.get("template") != template_id:
            continue
        field = spec["field"]

        if spec.get("group"):
            arr = _find_group_array(extracted_data, field)
            row = _pick_recent(arr, spec.get("recency_key")) if arr else None
            raw = None
            if row is not None:
                raw = row.get(field)
                if raw is None and spec.get("fallback_field"):
                    raw = row.get(spec["fallback_field"])
        else:
            raw = extracted_data.get(field) if isinstance(extracted_data, dict) else None
            if raw is None and spec.get("fallback_field") and isinstance(extracted_data, dict):
                raw = extracted_data.get(spec["fallback_field"])

        if raw is None:
            continue

        val = _date_to_years(raw, today=today) if spec.get("transform") == "date_to_years" else _to_number(raw)
        if val is not None:
            out[slot] = val

    return out
