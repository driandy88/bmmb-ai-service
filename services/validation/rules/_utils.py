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
