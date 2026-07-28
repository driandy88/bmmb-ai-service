"""
Graph nodes (brief §6, §7) — THIN. Each node delegates to one agent and knows
nothing about how the others work; all cross-cutting precedence lives in
routing.decide(). Nodes take (state, deps) and return partial state updates
(LangGraph merges them).

Pipeline: guardrail -> classify -> decide -> {refuse|clarify|handoff|dispatch|
canned} -> terminology -> audit -> END.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.orchestrator import routing
from app.utils import terminology

_NONE_UI = {"type": "none", "payload": {}}
_NO_HANDOFF = {"required": False, "reason": None, "contact": None}


# ── NLU + decision ───────────────────────────────────────────────────────────

def guardrail_node(state: dict, deps) -> dict:
    return {"guardrail": deps.guardrail.check(state["message"])}


def classify_node(state: dict, deps) -> dict:
    intent = deps.classifier.classify(state["message"], state.get("history", []))
    return {"intent": intent}


def decide_node(state: dict, deps) -> dict:
    active_flow_route = routing.STAGE_TO_ROUTE.get(state.get("stage") or "")
    d = routing.decide(
        deps.config.taxonomy, deps.config.responses,
        intent=state["intent"], guardrail=state["guardrail"],
        threshold=deps.settings.confidence_threshold,
        awaiting_clarification=state.get("awaiting_clarification", False),
        active_flow_route=active_flow_route,
    )
    return {"decision": d, "route": d.route_label, "rule_version": deps.config.rule_version}


def route_selector(state: dict) -> str:
    """Conditional-edge selector: the decided action names the next node."""
    return state["decision"].action


# ── Terminal action nodes ────────────────────────────────────────────────────

def refuse_node(state: dict, deps) -> dict:
    d: routing.RoutingDecision = state["decision"]
    reply = deps.config.responses.wording(d.refusal_ref) or deps.config.responses.wording("R6")
    return {
        "reply": reply, "ui_action": dict(_NONE_UI), "citations": [], "handoff": dict(_NO_HANDOFF),
        "stage": "refused", "awaiting_clarification": False,
        "decision_inputs": {"suppressed_in_scope": True, "adversarial_category": d.adversarial_category,
                            "guardrail_source": state.get("guardrail", {}).get("source")},
    }


def clarify_node(state: dict, deps) -> dict:
    reply = deps.config.responses.wording("R8", financing_product="SME financing")
    return {
        "reply": reply, "ui_action": dict(_NONE_UI), "citations": [], "handoff": dict(_NO_HANDOFF),
        "stage": "clarifying", "awaiting_clarification": True,
        "decision_inputs": {"clarification": True, "primary": state["intent"].get("primary")},
    }


def handoff_node(state: dict, deps) -> dict:
    """Low-confidence loop -> human handoff (T3)."""
    res = deps.sales_handoff.handle(state["message"], state.get("history", []),
                                    reason="T3", channel=state.get("channel", "customer"))
    return {
        "reply": res["reply"], "ui_action": res["ui_action"], "handoff": res["handoff_block"],
        "citations": [], "stage": "handoff", "awaiting_clarification": False,
        "decision_inputs": res["decision_inputs"],
    }


def canned_node(state: dict, deps) -> dict:
    """Out-of-scope redirect (R1–R5)."""
    d: routing.RoutingDecision = state["decision"]
    reply = deps.config.responses.wording(d.primary_ref)
    updates: dict[str, Any] = {
        "ui_action": dict(_NONE_UI), "citations": [], "handoff": dict(_NO_HANDOFF),
        "stage": "redirect", "decision_inputs": {"canned": d.primary_ref, "cat_id": d.primary_cat_id},
    }
    reply, extra = _apply_secondary(state, deps, reply)
    # An OOS primary won't meaningfully carry a secondary handler; drop the
    # dispatch-only internal keys if _apply_secondary produced any.
    extra.pop("route_secondary_citations", None)
    extra.pop("route_secondary_slots", None)
    updates.update(extra)
    updates["reply"] = reply
    return updates


def dispatch_node(state: dict, deps) -> dict:
    """In-scope handler(s). Runs the primary handler, folds in a handler-
    requested handoff (e.g. eligibility Tier-2), then applies the Sheet-9
    secondary action."""
    d: routing.RoutingDecision = state["decision"]
    res = _run_handler(deps, d.primary_ref, state)

    reply = res.get("reply", "") or ""
    ui = res.get("ui_action", dict(_NONE_UI))
    handoff_block = res.get("handoff_block") or dict(_NO_HANDOFF)
    stage = res.get("stage")
    citations = list(res.get("citations", []))
    decision_inputs = dict(res.get("decision_inputs", {}))
    updates: dict[str, Any] = {}
    if "slots" in res:
        updates["slots"] = res["slots"]

    # Handler REQUESTED a handoff it doesn't perform itself (eligibility Tier-2).
    if res.get("handoff") and not res.get("handoff_block"):
        h = deps.sales_handoff.handle(state["message"], state.get("history", []),
                                      reason=res.get("handoff_reason"), channel=state.get("channel", "customer"))
        reply = (reply + "\n\n" + h["reply"]).strip() if reply else h["reply"]
        ui, handoff_block, stage = h["ui_action"], h["handoff_block"], "handoff"
        decision_inputs = {**decision_inputs, **h["decision_inputs"]}

    # Sheet-9 secondary.
    reply, extra = _apply_secondary(state, deps, reply)
    if "route_secondary_citations" in extra:
        citations += extra.pop("route_secondary_citations")
    if "route_secondary_slots" in extra:
        updates["slots"] = {**updates.get("slots", state.get("slots", {})), **extra.pop("route_secondary_slots")}
    updates.update(extra)

    updates.update({"reply": reply, "ui_action": ui, "handoff": handoff_block,
                    "citations": citations, "stage": stage, "decision_inputs": decision_inputs})
    return updates


# ── Post-processing nodes ────────────────────────────────────────────────────

def terminology_node(state: dict, deps) -> dict:
    result = terminology.lint(state.get("reply", ""))
    di = dict(state.get("decision_inputs", {}))
    if result.violations:
        di["terminology_violations"] = result.violations
    return {"reply": result.text, "decision_inputs": di}


def audit_node(state: dict, deps) -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    intent = state.get("intent", {})
    record = {
        "trace_id": state.get("trace_id"),
        "session_id": state.get("session_id"),
        "channel": state.get("channel"),
        "route": state.get("route", ""),
        "rule_version": state.get("rule_version", ""),
        "guardrail": state.get("guardrail", {}),
        "intent": {"primary": intent.get("primary"), "confidence": intent.get("confidence"),
                   "secondary": intent.get("secondary")},
        "decision_inputs": state.get("decision_inputs", {}),
        "handoff_required": state.get("handoff", {}).get("required", False),
        "timestamp": ts,
    }
    deps.audit.write(record)   # append-only + re-redacted inside the writer
    return {"timestamp": ts}


# ── helpers ──────────────────────────────────────────────────────────────────

def _run_handler(deps, ref: str, state: dict) -> dict:
    """Dispatch a ROUTE-* to its agent. Returns the agent's raw result dict."""
    msg = state["message"]
    hist = state.get("history", [])
    slots = state.get("slots", {})
    if ref == "ROUTE-BRANCH":
        return deps.sales_handoff.handle(msg, hist, reason=None, channel=state.get("channel", "customer"))
    if ref == "ROUTE-PROGRAM":
        return deps.program_advisor.handle(msg, hist, slots)
    if ref in ("ROUTE-GUIDELINES", "ROUTE-SHARIAH"):
        return deps.guidelines.handle(msg, hist)
    if ref == "ROUTE-ELIGIBILITY":
        return deps.eligibility.handle(msg, hist, slots)
    if ref == "ROUTE-INITIATE":
        post = state.get("client_state", {}).get("stage") == "eligibility_done"
        return deps.initiate.handle(msg, hist, post_eligibility=post)
    if ref == "ROUTE-CONTINUE":
        return deps.lookup.handle(msg, hist, application_id=state.get("application_id"), mode="continue")
    if ref == "ROUTE-TRACK":
        return deps.lookup.handle(msg, hist, application_id=state.get("application_id"), mode="track")
    # Unknown ROUTE -> safe clarification.
    return {"reply": deps.config.responses.wording("R8", financing_product="SME financing"),
            "stage": "clarifying", "ui_action": dict(_NONE_UI), "citations": [],
            "handoff": False, "decision_inputs": {"unknown_route": ref}}


def _apply_secondary(state: dict, deps, reply: str) -> tuple[str, dict]:
    """Fold the Sheet-9 secondary action into `reply`. Returns (reply, extra
    state updates)."""
    d: routing.RoutingDecision = state["decision"]
    sec = getattr(d, "secondary", None)
    extra: dict[str, Any] = {}
    if not sec or sec.kind == "none":
        return reply, extra
    if sec.kind == "append_canned" and sec.ref:
        reply = (reply + "\n\n" + deps.config.responses.wording(sec.ref)).strip()
    elif sec.kind == "append_clarify":
        reply = (reply + "\n\n" + deps.config.responses.wording("R8", financing_product="SME financing")).strip()
        extra["awaiting_clarification"] = True
    elif sec.kind == "route_secondary" and sec.ref:
        res2 = _run_handler(deps, sec.ref, state)
        reply = (reply + "\n\n" + (res2.get("reply", "") or "")).strip()
        extra["route_secondary_citations"] = list(res2.get("citations", []))
        if "slots" in res2:
            extra["route_secondary_slots"] = res2["slots"]
    return reply, extra
