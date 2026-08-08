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

from app.agents.program_advisor.program_match import mentions_program
from app.orchestrator import routing
from app.utils import terminology
from app.utils.suggestions import explore_suggestions

# Intent buckets we're willing to override to a programme query: off-topic, social, and ambiguous.
# A genuine in-scope intent (apply INS-05, eligibility INS-04, track INS-07…) that happens to name a
# programme is left alone — only the classifier's "I don't recognise this" buckets get rescued.
_RESCUABLE_PREFIXES = ("OOS", "SOC", "AMB")
# …but a CONFIDENT, specific "other product" call — other bank products / non-SME financing /
# competitor / advice — is a real classification, not an "I don't recognise this". A programme name
# in "fixed deposit rate for GGSM" is incidental, so it must NOT hijack the turn into a programme
# query. The vague off-topic (OOS-05/06/07) / ambiguous buckets, where an unrecognised acronym like
# "what is GGSM" actually lands, still rescue.
_SPECIFIC_OTHER_PRODUCT = frozenset({"OOS-01", "OOS-02", "OOS-03", "OOS-04"})

_NONE_UI = {"type": "none", "payload": {}}
_NO_HANDOFF = {"required": False, "reason": None, "contact": None}


# ── NLU + decision ───────────────────────────────────────────────────────────

def guardrail_node(state: dict, deps) -> dict:
    return {"guardrail": deps.guardrail.check(state["message"])}


def classify_node(state: dict, deps) -> dict:
    intent = deps.classifier.classify(state["message"], state.get("history", []))
    # Deterministic rescue: the classifier doesn't know the programme acronyms, so "what is GGSM" /
    # "what is MIHP" intermittently lands in an off-topic / social / ambiguous bucket instead of
    # INS-02. If the message names a known programme and the LLM landed there, treat it as a
    # programme query so it reaches the advisor (adversarial still wins — the guardrail ran first).
    primary = intent.get("primary") or ""
    confidence = float(intent.get("confidence") or 0.0)
    threshold = getattr(getattr(deps, "settings", None), "confidence_threshold", 0.7)
    rescuable = (not primary or primary.startswith(_RESCUABLE_PREFIXES))
    # Yield to a confident "this is a specific OTHER product" call — don't let a stray programme name
    # ("…for GGSM") turn a fixed-deposit / personal-loan question into a programme query.
    confident_other_product = primary in _SPECIFIC_OTHER_PRODUCT and confidence >= threshold
    if rescuable and not confident_other_product and mentions_program(state["message"]):
        intent = {**intent, "primary": "INS-02", "confidence": max(confidence, 0.9)}
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
    # Smart clarify: a targeted question + tappable in-scope options ("did you mean…?"). Falls back
    # to the default R8 line + preset chips when the model can't offer a useful in-scope clarify
    # (or offline). Either way we ask ONCE — `awaiting_clarification` still hands off next turn.
    smart = deps.llm.generate_clarify(state["message"], state.get("history", []))
    question = (smart.get("question") or "").strip()
    options = smart.get("options") or []
    if question and options:
        reply, suggestions = question, options
    else:
        reply = deps.config.responses.wording("R8", financing_product="SME financing")
        suggestions = explore_suggestions()
    return {
        "reply": reply, "ui_action": dict(_NONE_UI), "citations": [], "handoff": dict(_NO_HANDOFF),
        "stage": "clarifying", "awaiting_clarification": True, "suggestions": suggestions,
        "decision_inputs": {"clarification": True, "primary": state["intent"].get("primary")},
    }


def handoff_node(state: dict, deps) -> dict:
    """Low-confidence loop -> human handoff (T3). Starts the sales-contact flow
    (intro + location IntakeCard); the client's next turn resolves the contact."""
    res = deps.sales_handoff.handle(state["message"], state.get("history", []),
                                    reason="T3", channel=state.get("channel", "customer"),
                                    stage=state.get("stage"))
    return {
        "reply": res["reply"], "ui_action": res["ui_action"], "handoff": res["handoff_block"],
        "citations": [], "stage": res.get("stage", "handoff"), "awaiting_clarification": False,
        "decision_inputs": res["decision_inputs"],
    }


def canned_node(state: dict, deps) -> dict:
    """Out-of-scope redirect (R1–R5) or social close (R9/R10/R11)."""
    d: routing.RoutingDecision = state["decision"]
    reply = deps.config.responses.wording(d.primary_ref)
    row = deps.config.taxonomy.get(d.primary_cat_id)
    last_program = (state.get("slots") or {}).get("last_program")

    # Done / dead-end turns don't have to be a full stop. A sign-off (SOC-03) or
    # an out-of-scope redirect re-surfaces the start-session presets as chips so
    # the customer always has a way forward — but the sign-off is REWORDED to
    # continue the thread (referencing what we just discussed), not to greet them
    # again like a new session.
    suggestions: list[dict] = []
    if d.primary_cat_id == "SOC-03":
        lead = f"Glad I could help with {last_program} today. " if last_program else "Glad I could help. "
        reply = lead + "If there's anything else, here are a few ways I can keep helping —"
        suggestions = explore_suggestions(last_program)
    elif row is not None and row.type == "out_of_scope":
        suggestions = explore_suggestions(last_program)
        # Smart, specific deflection: name what they actually asked ("fixed deposit rates…"), say it's
        # outside SME financing, and redirect — instead of the generic canned line. `compose` returns
        # the canned wording offline / on failure, so this only ever upgrades the reply (never the
        # adversarial refusals R6/R7, which aren't out_of_scope and don't reach here).
        reply = deps.llm.compose(
            "oos_deflection", message=state["message"], history=state.get("history", []),
            fallback=reply, category=(row.category or "another product or topic"),
        )

    updates: dict[str, Any] = {
        "ui_action": dict(_NONE_UI), "citations": [], "handoff": dict(_NO_HANDOFF),
        "suggestions": suggestions,
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
    primary_reply = reply
    ui = res.get("ui_action", dict(_NONE_UI))
    handoff_block = res.get("handoff_block") or dict(_NO_HANDOFF)
    stage = res.get("stage")
    citations = list(res.get("citations", []))
    sentences = res.get("sentences")          # grounded RAG answer (Phase 1), else None
    grounded = bool(res.get("grounded"))
    suggestions = list(res.get("suggestions", []))   # next-step chips, any handler may attach
    decision_inputs = dict(res.get("decision_inputs", {}))
    updates: dict[str, Any] = {}
    if "slots" in res:
        updates["slots"] = res["slots"]

    # Handler REQUESTED a handoff it doesn't perform itself (frozen eligibility T4,
    # etc.). Start the sales-contact flow (intro + location IntakeCard); its stage
    # (await_contact_location) carries through so the next turn resolves the contact.
    if res.get("handoff") and not res.get("handoff_block"):
        h = deps.sales_handoff.handle(state["message"], state.get("history", []),
                                      reason=res.get("handoff_reason"), channel=state.get("channel", "customer"))
        reply = (reply + "\n\n" + h["reply"]).strip() if reply else h["reply"]
        ui, handoff_block, stage = h["ui_action"], h["handoff_block"], h.get("stage", "handoff")
        decision_inputs = {**decision_inputs, **h["decision_inputs"]}

    # Sheet-9 secondary.
    reply, extra = _apply_secondary(state, deps, reply)
    if "route_secondary_citations" in extra:
        citations += extra.pop("route_secondary_citations")
    if "route_secondary_slots" in extra:
        updates["slots"] = {**updates.get("slots", state.get("slots", {})), **extra.pop("route_secondary_slots")}
    updates.update(extra)

    # If a handoff or Sheet-9 secondary appended text, the sentence→cite map no
    # longer covers the whole reply — drop it so the UI renders the plain combined
    # reply instead of chips over only part of it.
    if reply != primary_reply:
        sentences, grounded = None, False

    updates.update({"reply": reply, "ui_action": ui, "handoff": handoff_block,
                    "citations": citations, "sentences": sentences, "grounded": grounded,
                    "suggestions": suggestions, "stage": stage, "decision_inputs": decision_inputs})
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
        # Pass the client stage so turn 2 (await_contact_location) resolves the
        # contact from the location the customer just gave. Same field the routing
        # continuation keys on (STAGE_TO_ROUTE.get(state["stage"])).
        return deps.sales_handoff.handle(msg, hist, reason=None, channel=state.get("channel", "customer"),
                                         stage=state.get("stage"))
    if ref == "ROUTE-PROGRAM":
        return deps.program_advisor.handle(msg, hist, slots, stage=state.get("stage"),
                                           intent=state.get("intent"))
    if ref in ("ROUTE-GUIDELINES", "ROUTE-SHARIAH"):
        return deps.guidelines.handle(msg, hist)
    if ref == "ROUTE-ELIGIBILITY":
        return deps.eligibility.handle(msg, hist, slots)
    if ref == "ROUTE-INITIATE":
        post = state.get("client_state", {}).get("stage") == "eligibility_done"
        return deps.initiate.handle(msg, hist, post_eligibility=post,
                                    program=slots.get("last_program"))
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
