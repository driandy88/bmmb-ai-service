"""
`get_sales_contact` — resolve a Malaysian state or city to the named SME contact.

The geo lookup is v1's, reimplemented over the same vendored sales_directory.yaml
(v1's version lives inside the SalesHandoff class alongside its two-turn flow
machinery, which v2 does not want — the agent runs the conversation itself).

What is NOT carried over is v1's `detect_triggers()` keyword list — the substring
scan for "rude", "human", "representative" that decides whether a handoff is
warranted. Judging that is the agent's job now, and it is one of the places where
keyword matching most obviously failed.
"""
from __future__ import annotations

from functools import lru_cache

from langchain_core.tools import tool

from app.config.loader import load_config
from app.config.settings import get_settings
from app.runtime.context import current


@lru_cache(maxsize=1)
def _indexes() -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """(state -> region_id, city -> (region_id, state)), lowercased once."""
    sales = load_config().sales
    states: dict[str, str] = {}
    cities: dict[str, tuple[str, str]] = {}
    for state, entry in sales["geo"].items():
        rid = entry["region_id"]
        states[state.lower()] = rid
        for city in entry.get("cities", []):
            cities[city.lower()] = (rid, state)
    return states, cities


def _resolve_region(text: str) -> dict:
    """States first, longest name first so 'Negeri Sembilan' doesn't lose to a
    substring; then cities. Unresolvable falls back to the general team rather
    than failing — a customer who won't say where they are still needs someone."""
    sales = load_config().sales
    low = (text or "").lower()
    states, cities = _indexes()

    for state in sorted(states, key=len, reverse=True):
        if state in low:
            rid = states[state]
            return {"region_id": rid, "region": sales["regions"][rid]["region"], "matched": state.title()}
    for city in sorted(cities, key=len, reverse=True):
        if city in low:
            rid, _ = cities[city]
            return {"region_id": rid, "region": sales["regions"][rid]["region"], "matched": city.title()}

    rid = sales.get("fallback_region_id", "R1")
    return {"region_id": rid, "region": sales["regions"][rid]["region"], "matched": None}


@tool
def get_sales_contact(location: str) -> str:
    """Get the Bank Muamalat SME financing contact for a Malaysian state or city.

    Call this when handing off to a human: when the customer asks for a person,
    complains about service, or when you cannot help and they need someone who can.
    Ask which state or city they are in first.

    If the location does not match, this tells you the valid states so you can
    translate what the customer said and call again. Pass "general" when they will
    not say where they are, or when you have asked and still cannot tell.

    Never invent a name, phone number or email. Only pass on what this returns.

    Args:
        location: a Malaysian state or city, e.g. "Johor Bahru", "Selangor".
            Pass "general" for the nationwide team.
    """
    ctx = current()
    sales = load_config().sales

    if (location or "").strip().lower() in ("", "general", "any", "unknown", "nationwide"):
        rid = sales.get("fallback_region_id", "R1")
        region = {"region_id": rid, "region": sales["regions"][rid]["region"], "matched": "general"}
    else:
        region = _resolve_region(location)

    # Unmatched: hand the reasoning back rather than silently defaulting. The
    # directory only knows official state and city names, so "JB", "KL", a
    # misspelling or a district name misses — all things the agent can resolve
    # if it is told what the valid answers are. v1 just fell through to the
    # general team here, which quietly downgraded a resolvable handoff.
    if region["matched"] is None:
        states = ", ".join(sorted(s.title() for s in _indexes()[0]))
        ctx.record("get_sales_contact", matched=None, result="unresolved")
        return (
            f"UNRESOLVED: '{location}' did not match a state or city in the directory.\n"
            f"Known states: {states}.\n\n"
            "If you can tell which of these the customer means (an abbreviation like "
            "'JB' or 'KL', a district, or a misspelling), call this tool again with "
            "that state name. If you genuinely cannot tell, ask them which state they "
            "are in. Only fall back to the general team if they will not say."
        )

    contact = dict(sales["regions"][region["region_id"]]["contacts"][0])
    contact["region"] = region["region"]
    contact["hours"] = get_settings().handoff_hours

    ctx.record("get_sales_contact", matched=region["matched"], region_id=region["region_id"])
    ctx.set_ui("show_contact_card", **contact)
    ctx.handoff = {"required": True, "reason": "sales_contact", "contact": contact}

    who = contact.get("employee", "our SME financing team")
    reach = ", ".join(x for x in (contact.get("phone"), contact.get("email")) if x)
    matched = (
        f"Nationwide {region['region']} team."
        if region["matched"] == "general"
        else f"Resolved '{region['matched']}' to the {region['region']} region."
    )
    return (
        f"{matched}\nContact: {who}" + (f" ({reach})" if reach else "")
        + f"\nAvailable {contact['hours']}.\n\n"
        "The client is rendering this as a contact card. Give the name warmly in "
        "one sentence; do not repeat the full contact details as a list."
    )