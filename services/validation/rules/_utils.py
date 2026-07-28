import difflib
from datetime import date, datetime
from typing import Iterable, Optional, Tuple, Union

from ..domain.policies import ValidationPolicy

DateLike = Union[str, date, datetime]

def to_date(value: DateLike) -> date:
    """Coerce a date, datetime, or ISO 'YYYY-MM-DD' string into a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"Cannot convert {value!r} of type {type(value)} to date")


def normalize_id(value: str) -> str:
    """Normalize an identifier (e.g. NRIC/passport number) for cross-document matching."""
    return "".join(ch for ch in value.upper() if ch.isalnum())


# The two identity-document types BMMB accepts for a director, and the raw
# "ID Type" strings extraction has been seen to return for each. Anything
# outside this table stays unknown (None) rather than being guessed at --
# see normalize_id_type.
ID_TYPE_MYKAD = "mykad"
ID_TYPE_PASSPORT = "passport"
_ID_TYPE_ALIASES = {
    "mykad": ID_TYPE_MYKAD,
    "my kad": ID_TYPE_MYKAD,
    "mykad ic": ID_TYPE_MYKAD,
    "ic": ID_TYPE_MYKAD,
    "nric": ID_TYPE_MYKAD,
    "identity card": ID_TYPE_MYKAD,
    "identification card": ID_TYPE_MYKAD,
    "kad pengenalan": ID_TYPE_MYKAD,
    "passport": ID_TYPE_PASSPORT,
    "pasport": ID_TYPE_PASSPORT,
    "international passport": ID_TYPE_PASSPORT,
    "passport number": ID_TYPE_PASSPORT,
}


def normalize_id_type(value: object) -> Optional[str]:
    """Resolve a raw "ID Type" string to ID_TYPE_MYKAD, ID_TYPE_PASSPORT, or None.

    None means "not stated / not recognized", which callers must treat as the
    stricter MyKad requirement rather than as a passport -- an unreadable ID
    Type must never be able to *weaken* a check (TICKET-6).
    """
    if not isinstance(value, str):
        return None
    return _ID_TYPE_ALIASES.get(" ".join(value.strip().lower().split()))


# The adapter substitutes this for a missing/null Customer Information Form
# field instead of "" (see extraction_adapter.build_customer_information_doc),
# so it reads unambiguously in the report rather than looking like an empty
# cell. Completeness checks must still treat it as missing -- see is_blank().
NOT_AVAILABLE = "Not Available"


def is_blank(value: object) -> bool:
    """True for a genuinely-empty field: unset, "", or the NOT_AVAILABLE placeholder."""
    return not value or value == NOT_AVAILABLE


def best_fuzzy_match(normalized: str, candidates: Iterable[str]) -> Tuple[Optional[str], float]:
    """Best difflib similarity match for `normalized` among `candidates`, with its score.

    Shared by the alias resolvers (bank name, entity type) -- same "normalize,
    then score every known candidate, keep the best" shape, just against
    different candidate sets.
    """
    best_candidate = None
    best_score = 0.0
    for candidate in candidates:
        score = difflib.SequenceMatcher(None, normalized, candidate).ratio()
        if score > best_score:
            best_score = score
            best_candidate = candidate
    return best_candidate, best_score


def resolve_entity_type_key(entity_type: str, policy: ValidationPolicy, threshold: float = 0.85) -> Optional[str]:
    """Resolve a raw entity_type string to one of policy's own canonical
    entity keys, tolerating typos and Malay-language variants (e.g.
    "Perniagaan Tunggal", "Enterprise", "Perkongsian", "llp") via
    policy.entity_type_aliases, then a conservative fuzzy-match fallback for
    variants that table hasn't seen yet -- so an unrecognized spelling of a
    real sole-prop/partnership entity doesn't silently resolve to the lenient
    Sdn-Bhd-shaped default. Returns None (callers fall through to their own
    default) only when nothing -- exact, alias, or fuzzy -- resolves
    confidently; a genuinely blank/unknown entity_type is a distinct,
    deliberately out-of-scope problem (TICKET-9/D6).

    Lives here rather than in any one rule module because two unrelated rules
    key off the same resolved entity type: the bank-statement minimum
    (rules/date_logic.py) and the mandatory-document slots
    (rules/completeness.py).
    """
    normalized = entity_type.strip().lower()
    if not normalized:
        return None
    if normalized in policy.minimum_bank_statement_months_by_entity:
        return normalized
    if normalized in policy.entity_type_aliases:
        return policy.entity_type_aliases[normalized]

    known_keys = set(policy.minimum_bank_statement_months_by_entity) | set(policy.entity_type_aliases.values())
    best_key, best_score = best_fuzzy_match(normalized, known_keys)
    if best_key is not None and best_score >= threshold:
        return best_key
    return None
