"""
SessionState — the LangGraph state object threaded through every node.

Memory model (brief §3, §5.1): the client is the source of truth for
conversational memory. Each turn we HYDRATE this state fresh from the request
(history + client_state), run one pass of the graph, and echo the updated
short-memory back in the response. The checkpointer (session_store) is optional
durability, not the memory of record — so we never trust client_state as
authoritative for critical outcomes.
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class SessionState(TypedDict, total=False):
    # ── Identity / request (hydrated each turn) ──────────────────────────────
    session_id: str
    trace_id: str
    channel: str                       # customer | branch
    application_id: Optional[str]
    message: str                       # current utterance (raw)
    history: list[dict[str, str]]      # [{role, content}] — server-trimmed
    client_state: dict[str, Any]       # untrusted convenience cache from client

    # ── Short-memory (authoritative slots the server owns) ───────────────────
    slots: dict[str, Any]              # collected eligibility slots, etc.
    stage: Optional[str]               # e.g. eligibility_slotfill | clarifying
    last_intent: Optional[str]
    awaiting_clarification: bool       # True if we asked R8 last turn (Sheet 9.4)

    # ── NLU outputs ──────────────────────────────────────────────────────────
    guardrail: dict[str, Any]          # {flagged, category}
    intent: dict[str, Any]             # {primary, confidence, secondary}
    decision: dict[str, Any]           # RoutingDecision (routing.py) as dict

    # ── Handler outputs (assembled into the envelope) ────────────────────────
    reply: str
    route: str                         # human-readable route label for audit
    ui_action: dict[str, Any]          # {type, payload}
    citations: list[dict[str, Any]]    # [{corpus, ref, snippet}]
    sentences: Optional[list[dict[str, Any]]]  # grounded RAG answer (Phase 1): [{text, cites}]
    grounded: bool                     # True when the reply is a cited, grounded RAG answer
    handoff: dict[str, Any]            # {required, reason, contact}
    decision_inputs: dict[str, Any]    # PII-redacted inputs for audit
    rule_version: str
    timestamp: str                     # ISO-8601, stamped by the audit node


def new_state() -> SessionState:
    """A blank state with the mutable containers initialised."""
    return SessionState(
        slots={},
        history=[],
        client_state={},
        guardrail={"flagged": False, "category": None},
        intent={"primary": None, "confidence": 0.0, "secondary": None},
        decision={},
        reply="",
        route="",
        ui_action={"type": "none", "payload": {}},
        citations=[],
        handoff={"required": False, "reason": None, "contact": None},
        decision_inputs={},
        awaiting_clarification=False,
    )
