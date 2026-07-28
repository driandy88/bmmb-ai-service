"""
Deterministic routing — the Sheet-9 precedence engine (brief §4.7).

`decide()` is a PURE function of the classifier output, the guardrail verdict,
and a little session state. No LLM, no I/O — this is where "deterministic where
it matters" lives, and it is unit-tested directly (tests/test_routing.py,
notebook Part A).

Precedence (highest first):
  1. Adversarial (guardrail flagged OR primary/secondary is ADV) -> REFUSE via
     R6/R7, SUPPRESS any in-scope answer, log. (Sheet 9.1 rows 3 & 5)
  2. Low confidence (< threshold) -> CLARIFY (R8); if we already clarified last
     turn -> HANDOFF. Never ask twice. (Sheet 9.4)
  3. Otherwise route the primary intent, and fold in the secondary per Sheet 9.1:
       in + out      -> dispatch primary, append the out-of-scope redirect
       in + in       -> dispatch primary, then dispatch secondary
       in + unclear  -> dispatch primary, append R8 clarification
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.config.loader import Responses, Taxonomy

# ROUTE-* (from intents.yaml) -> (graph node key, human-readable audit label).
# This is dispatch WIRING, not taxonomy data — the ROUTE-* strings themselves
# come from the YAML. Adding a ROUTE-* target needs a node + one row here.
ROUTE_TABLE: dict[str, tuple[str, str]] = {
    "ROUTE-BRANCH":      ("sales_handoff",   "2.0 Reroute to Sales"),
    "ROUTE-PROGRAM":     ("program_advisor", "3.0 Program queries"),
    "ROUTE-GUIDELINES":  ("guidelines",      "4.0 Guidelines / Shariah"),
    "ROUTE-SHARIAH":     ("guidelines",      "4.0 Guidelines / Shariah (boundary business)"),
    "ROUTE-ELIGIBILITY": ("eligibility",     "5.0 In-principle eligibility"),
    "ROUTE-INITIATE":    ("initiate",        "6.0 Initiate new application"),
    "ROUTE-CONTINUE":    ("lookup",          "7.0 Continue draft"),
    "ROUTE-TRACK":       ("lookup",          "8.0 Track application"),
}

# Actions the graph dispatches on.
REFUSE = "refuse"
CLARIFY = "clarify"
HANDOFF = "handoff"
DISPATCH = "dispatch"     # in-scope handler
CANNED = "canned"         # OOS canned redirect (R1–R5)

# Active multi-turn flows: when the client echoes one of these stages, a bare
# follow-up ("4 years old", "machinery") continues the SAME handler instead of
# being re-classified from scratch — unless the turn is adversarial or the user
# clearly switched to a different in-scope intent. (Client stage is an untrusted
# convenience cache — safe to use for flow continuity since it grants no
# authorization; guardrails still run every turn and verdicts stay server-side.)
STAGE_TO_ROUTE: dict[str, str] = {
    "eligibility_slotfill": "ROUTE-ELIGIBILITY",
    "funnel_purpose": "ROUTE-PROGRAM",
    "funnel_amount": "ROUTE-PROGRAM",
    "await_application_id_continue": "ROUTE-CONTINUE",
    "await_application_id_track": "ROUTE-TRACK",
}


@dataclass
class SecondaryAction:
    kind: str                      # append_canned | route_secondary | append_clarify | none
    cat_id: Optional[str] = None
    ref: Optional[str] = None      # canned response ref, if any
    handler: Optional[str] = None  # secondary handler node, if in-scope


@dataclass
class RoutingDecision:
    action: str
    route_label: str
    primary_cat_id: Optional[str] = None
    primary_ref: Optional[str] = None
    primary_handler: Optional[str] = None
    refusal_ref: Optional[str] = None
    secondary: SecondaryAction = field(default_factory=lambda: SecondaryAction("none"))
    adversarial: bool = False
    adversarial_category: Optional[str] = None
    reason: Optional[str] = None


def _is_clarification(responses: Responses, ref: Optional[str]) -> bool:
    """A non-terminal canned response (R8) = a clarification that must not
    repeat. Data-driven via the `terminal:` flag in responses.yaml."""
    strat = responses.get(ref)
    return bool(strat and not strat.terminal)


def _clarify_or_handoff(awaiting_clarification: bool) -> tuple[str, str, Optional[str]]:
    """Sheet 9.4: clarify once; if still unresolved, hand off (T3). Never twice."""
    if awaiting_clarification:
        return HANDOFF, "2.0 Reroute to Sales (low-confidence loop / T3)", "clarification_unresolved"
    return CLARIFY, "Clarification (R8)", None


def decide(
    taxonomy: Taxonomy,
    responses: Responses,
    *,
    intent: dict,
    guardrail: dict,
    threshold: float,
    awaiting_clarification: bool = False,
    active_flow_route: Optional[str] = None,
) -> RoutingDecision:
    primary_id = intent.get("primary")
    secondary_id = intent.get("secondary")
    confidence = float(intent.get("confidence") or 0.0)

    primary_row = taxonomy.get(primary_id)
    secondary_row = taxonomy.get(secondary_id)

    # ── 1. Adversarial precedence (Sheet 9.1) ───────────────────────────────
    adv_from_guard = bool(guardrail.get("flagged"))
    adv_primary = bool(primary_row and primary_row.type == "adversarial")
    adv_secondary = bool(secondary_row and secondary_row.type == "adversarial")
    if adv_from_guard or adv_primary or adv_secondary:
        # Pick the adversarial category (guardrail wins; else whichever slot).
        adv_id = None
        if adv_from_guard and guardrail.get("category"):
            adv_id = guardrail["category"]
        elif adv_primary:
            adv_id = primary_id
        elif adv_secondary:
            adv_id = secondary_id
        adv_row = taxonomy.get(adv_id)
        refusal_ref = (adv_row.response_ref if adv_row else "R6") or "R6"
        return RoutingDecision(
            action=REFUSE,
            route_label="Refusal (adversarial)",
            primary_cat_id=primary_id,
            refusal_ref=refusal_ref,
            adversarial=True,
            adversarial_category=adv_id,
            reason="adversarial",
        )

    # ── 1.5 Active-flow continuation ────────────────────────────────────────
    # Mid slot-fill (eligibility / program funnel / application-id await), a
    # bare follow-up continues the same handler — unless the user clearly
    # switched to a DIFFERENT high-confidence in-scope route.
    if active_flow_route and active_flow_route in ROUTE_TABLE:
        switched = (
            primary_row is not None and primary_row.is_route
            and confidence >= threshold
            and primary_row.response_ref != active_flow_route
        )
        if not switched:
            handler, label = ROUTE_TABLE[active_flow_route]
            return RoutingDecision(
                action=DISPATCH, route_label=f"{label} (cont.)",
                primary_cat_id=primary_id, primary_ref=active_flow_route, primary_handler=handler,
            )

    # ── 2. Confidence gate (Sheet 9.4) ──────────────────────────────────────
    if confidence < threshold or primary_row is None:
        action, label, reason = _clarify_or_handoff(awaiting_clarification)
        return RoutingDecision(
            action=action, route_label=label,
            primary_cat_id=primary_id, primary_ref="R8", reason=reason,
        )

    # ── 3. Primary is an intent-driven clarification (AMB-02/03/06 -> R8) ────
    if not primary_row.is_route and _is_clarification(responses, primary_row.response_ref):
        action, label, reason = _clarify_or_handoff(awaiting_clarification)
        return RoutingDecision(
            action=action, route_label=label,
            primary_cat_id=primary_id, primary_ref=primary_row.response_ref, reason=reason,
        )

    # ── 4. Build the secondary action (only meaningful when primary answers) ─
    secondary = SecondaryAction("none")
    if secondary_row is not None and secondary_row.type != "adversarial":
        if secondary_row.type == "in_scope" and secondary_row.is_route:
            handler = ROUTE_TABLE.get(secondary_row.response_ref, (None, ""))[0]
            secondary = SecondaryAction("route_secondary", cat_id=secondary_id,
                                        ref=secondary_row.response_ref, handler=handler)
        elif secondary_row.type == "out_of_scope":
            secondary = SecondaryAction("append_canned", cat_id=secondary_id, ref=secondary_row.response_ref)
        elif secondary_row.type == "ambiguous":
            if _is_clarification(responses, secondary_row.response_ref):
                secondary = SecondaryAction("append_clarify", cat_id=secondary_id, ref="R8")
            elif secondary_row.is_route:
                handler = ROUTE_TABLE.get(secondary_row.response_ref, (None, ""))[0]
                secondary = SecondaryAction("route_secondary", cat_id=secondary_id,
                                            ref=secondary_row.response_ref, handler=handler)
            else:
                secondary = SecondaryAction("append_canned", cat_id=secondary_id, ref=secondary_row.response_ref)

    # ── 5. Dispatch the primary ─────────────────────────────────────────────
    if primary_row.is_route:
        handler, label = ROUTE_TABLE.get(primary_row.response_ref, (None, "Unknown route"))
        return RoutingDecision(
            action=DISPATCH, route_label=label,
            primary_cat_id=primary_id, primary_ref=primary_row.response_ref,
            primary_handler=handler, secondary=secondary,
        )

    # Primary is a terminal canned OOS redirect (R1–R5).
    return RoutingDecision(
        action=CANNED, route_label=f"Redirect ({primary_row.response_ref})",
        primary_cat_id=primary_id, primary_ref=primary_row.response_ref, secondary=secondary,
    )
