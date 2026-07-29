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


# Every way a form or an extraction result says "there is nothing here".
# Applicants write "N/A" or "-" in a cell that doesn't apply to them, and
# extraction passes that through verbatim; treating those as filled-in values
# is how an empty cell passes a completeness check. Compared case-folded with
# punctuation stripped, so "N.A." and "n/a" both land here.
_NOT_AVAILABLE_SENTINELS = {
    "", "na", "n a", "nil", "none", "null", "-", "--",
    "notavailable", "not available", "notapplicable", "not applicable",
    "tiada",  # Malay: "none"
}


def is_blank(value: object) -> bool:
    """True for a genuinely-empty field: unset, "", or any "not available" sentinel.

    A cell reading "N/A" is empty, not filled -- see _NOT_AVAILABLE_SENTINELS.
    Note this makes a field's *absence* indistinguishable from an explicit
    "not applicable"; where that difference matters (spouse details for an
    unmarried director), the caller decides whether the field was required at
    all rather than relying on this to tell them.
    """
    if value is None:
        return True
    if not isinstance(value, str):
        return not value
    collapsed = " ".join(value.strip().lower().split())
    stripped = "".join(ch for ch in collapsed if ch.isalnum() or ch in " -")
    return stripped in _NOT_AVAILABLE_SENTINELS


# Currency renderings that mean the same currency. Malaysian statements print
# the local currency as "RM" at least as often as "MYR", and extraction passes
# through whatever the document shows -- without this, an "RM" statement reads
# as a foreign currency needing conversion into itself.
#
# Deliberately absent: a bare "$". It could be USD, SGD, AUD or several
# others, and guessing would mean converting at the wrong rate silently. It
# falls through to the passthrough below, fails the match, and gets looked at.
_CURRENCY_ALIASES = {
    "RM": "MYR",
    "RM.": "MYR",
    "R.M.": "MYR",
    "MYR (RM)": "MYR",
    "RINGGIT": "MYR",
    "RINGGIT MALAYSIA": "MYR",
    "MALAYSIAN RINGGIT": "MYR",
    "S$": "SGD",
    "SGD$": "SGD",
    "SINGAPORE DOLLAR": "SGD",
    "US$": "USD",
    "USD$": "USD",
    "US DOLLAR": "USD",
    "U.S. DOLLAR": "USD",
}


def normalize_currency(value: object) -> Optional[str]:
    """Resolve a currency rendering to its ISO code, case- and spacing-insensitive.

    "RM", "rm", "Ringgit Malaysia" and "MYR" are one currency. An
    unrecognized-but-real code ("GBP") passes through uppercased rather than
    becoming None: it's a comparable currency this table simply hasn't seen,
    and it should register as a mismatch to convert, not as a missing value.
    None is reserved for a genuinely absent currency.
    """
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.strip().upper().split())
    if not collapsed:
        return None
    return _CURRENCY_ALIASES.get(collapsed, collapsed)


MARITAL_STATUS_MARRIED = "married"
MARITAL_STATUS_UNMARRIED = "unmarried"
# Whether a director has a spouse to name. Malay terms included because the
# forms are bilingual: bujang (single), bercerai (divorced), balu/janda
# (widow), duda (widower), berkahwin/kahwin (married).
_MARITAL_STATUS_ALIASES = {
    "married": MARITAL_STATUS_MARRIED,
    "berkahwin": MARITAL_STATUS_MARRIED,
    "kahwin": MARITAL_STATUS_MARRIED,
    "single": MARITAL_STATUS_UNMARRIED,
    "bujang": MARITAL_STATUS_UNMARRIED,
    "divorced": MARITAL_STATUS_UNMARRIED,
    "bercerai": MARITAL_STATUS_UNMARRIED,
    "widowed": MARITAL_STATUS_UNMARRIED,
    "widow": MARITAL_STATUS_UNMARRIED,
    "widower": MARITAL_STATUS_UNMARRIED,
    "balu": MARITAL_STATUS_UNMARRIED,
    "janda": MARITAL_STATUS_UNMARRIED,
    "duda": MARITAL_STATUS_UNMARRIED,
    "separated": MARITAL_STATUS_UNMARRIED,
}


def normalize_marital_status(value: object) -> Optional[str]:
    """Resolve a marital status to MARITAL_STATUS_MARRIED/UNMARRIED, or None.

    None means "not stated or not recognized" -- the caller can't then tell
    whether spouse details were required, which is a needs-review situation
    rather than something to guess at in either direction.
    """
    if not isinstance(value, str):
        return None
    return _MARITAL_STATUS_ALIASES.get(" ".join(value.strip().lower().split()))


# How many offending items a failure message names before it stops listing
# and says how many are left. A message is read at a glance in a report; the
# full list always stays in `details`, so nothing is lost by capping here.
MESSAGE_ITEM_LIMIT = 5


def summarize_items(items: Iterable[str], limit: int = MESSAGE_ITEM_LIMIT) -> str:
    """Join items for a failure message, capping the list at `limit`.

    Rules name the specific things that failed rather than just counting them
    -- "missing Cash Flow on FYE 2025-12-31" instead of "1 statement is
    incomplete" -- so a reviewer doesn't have to open `details` to find out
    what to look at. A bundle with dozens of problems would blow the message
    out, hence the cap and the "(+N more)" tail.
    """
    items = list(items)
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f" (+{len(items) - limit} more)"


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
