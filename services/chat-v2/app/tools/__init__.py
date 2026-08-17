"""
The toolbelt.

Every tool here wraps something deterministic — a rules engine, a YAML lookup, a
vector search, bank-approved wording. None of them ask the model to decide
anything a bank would not let it decide. That split is the whole design: the
agent is free to plan, sequence, combine and phrase; it is not free to invent a
profit rate, pick a product, issue a verdict, or write a refusal.

Order matters slightly — it is the order the model sees them listed, and the
knowledge search leads because it is the one most turns need.
"""
from __future__ import annotations

from app.tools.application import lookup_application, start_application
from app.tools.eligibility import check_eligibility
from app.tools.programmes import search_programmes
from app.tools.responses import get_approved_response
from app.tools.retrieval import search_knowledge
from app.tools.sales import get_sales_contact

ALL_TOOLS = [
    search_knowledge,
    search_programmes,
    get_sales_contact,
    start_application,
    lookup_application,
    check_eligibility,
    get_approved_response,
]

__all__ = [
    "ALL_TOOLS",
    "search_knowledge",
    "search_programmes",
    "get_sales_contact",
    "start_application",
    "lookup_application",
    "check_eligibility",
    "get_approved_response",
]