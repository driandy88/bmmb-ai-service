from datetime import date, datetime
from typing import Union

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


# The adapter substitutes this for a missing/null Customer Information Form
# field instead of "" (see extraction_adapter.build_customer_information_doc),
# so it reads unambiguously in the report rather than looking like an empty
# cell. Completeness checks must still treat it as missing -- see is_blank().
NOT_AVAILABLE = "Not Available"


def is_blank(value: object) -> bool:
    """True for a genuinely-empty field: unset, "", or the NOT_AVAILABLE placeholder."""
    return not value or value == NOT_AVAILABLE
