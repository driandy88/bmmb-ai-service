"""
The orchestrator (brief §3, §7) — builds the LangGraph state machine, assembles
the dependency container, and exposes `Orchestrator.handle(ChatRequest) ->
ChatResponse`.

Graph (nodes = concerns, conditional edge = Sheet-9 dispatch):
  guardrail -> classify -> decide -> {refuse|clarify|handoff|dispatch|canned}
            -> terminology -> audit -> END

Memory is client-supplied (§5.1): each turn is hydrated fresh from the request,
run once, and the updated short-memory is echoed back. The checkpointer is
durability/resume only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from langgraph.graph import END, START, StateGraph

from app.agents.eligibility.agent import EligibilityAgent
from app.agents.guardrail.guardrail import Guardrail
from app.agents.guidelines.guidelines import GuidelinesAgent
from app.agents.intent_classifier.classifier import IntentClassifier
from app.agents.program_advisor.advisor import ProgramAdvisor
from app.agents.rag.corpora import get_retriever
from app.agents.rag.retriever import Retriever
from app.agents.sales_handoff.handoff import SalesHandoff
from app.agents.application.initiate import Initiate
from app.agents.application.lookup import ApplicationLookup
from app.api.schemas import (
    AuditBlock, ChatRequest, ChatResponse, Citation, GuardrailVerdict, HandoffBlock,
    HandoffContact, IntentBlock, ResponseState, UiAction,
)
from app.config.loader import AppConfig, load_config
from app.config.settings import Settings, get_settings
from app.integrations.audit import AuditWriter, get_audit_writer
from app.integrations.extraction import ExtractionClient, get_extraction_client
from app.integrations.llm import LLMClient, get_llm_client
from app.integrations.session_store import get_checkpointer
from app.orchestrator import nodes
from app.orchestrator.state import SessionState, new_state
from app.utils import pii

_UI_TYPES = {"none", "render_eligibility_form", "show_eligibility_result",
             "open_application_link", "show_contact_card", "show_program_options"}


@dataclass
class Deps:
    config: AppConfig
    settings: Settings
    llm: LLMClient
    retriever: Retriever
    audit: AuditWriter
    checkpointer: object
    guardrail: Guardrail
    classifier: IntentClassifier
    eligibility: EligibilityAgent
    program_advisor: ProgramAdvisor
    guidelines: GuidelinesAgent
    sales_handoff: SalesHandoff
    initiate: Initiate
    lookup: ApplicationLookup
    extraction: ExtractionClient


def build_deps(settings: Optional[Settings] = None) -> Deps:
    settings = settings or get_settings()
    config = load_config()
    llm = get_llm_client(settings)
    retriever = get_retriever(settings)
    return Deps(
        config=config, settings=settings, llm=llm, retriever=retriever,
        audit=get_audit_writer(settings), checkpointer=get_checkpointer(settings),
        guardrail=Guardrail(llm),
        classifier=IntentClassifier(llm),
        eligibility=EligibilityAgent(llm, config),
        program_advisor=ProgramAdvisor(llm, retriever, config),
        guidelines=GuidelinesAgent(llm, retriever, config),
        sales_handoff=SalesHandoff(config),
        initiate=Initiate(),
        lookup=ApplicationLookup(),
        extraction=get_extraction_client(settings),
    )


def build_graph(deps: Deps):
    # Node names must not collide with SessionState keys (LangGraph rule), so
    # "screen"/"human_handoff" stand in for the guardrail/handoff concerns.
    b = StateGraph(SessionState)
    b.add_node("screen", lambda s: nodes.guardrail_node(s, deps))
    b.add_node("classify", lambda s: nodes.classify_node(s, deps))
    b.add_node("decide", lambda s: nodes.decide_node(s, deps))
    b.add_node("refuse", lambda s: nodes.refuse_node(s, deps))
    b.add_node("clarify", lambda s: nodes.clarify_node(s, deps))
    b.add_node("human_handoff", lambda s: nodes.handoff_node(s, deps))
    b.add_node("dispatch", lambda s: nodes.dispatch_node(s, deps))
    b.add_node("canned", lambda s: nodes.canned_node(s, deps))
    b.add_node("terminology", lambda s: nodes.terminology_node(s, deps))
    b.add_node("audit", lambda s: nodes.audit_node(s, deps))

    b.add_edge(START, "screen")
    b.add_edge("screen", "classify")
    b.add_edge("classify", "decide")
    b.add_conditional_edges("decide", nodes.route_selector, {
        "refuse": "refuse", "clarify": "clarify", "handoff": "human_handoff",
        "dispatch": "dispatch", "canned": "canned",
    })
    for terminal in ("refuse", "clarify", "human_handoff", "dispatch", "canned"):
        b.add_edge(terminal, "terminology")
    b.add_edge("terminology", "audit")
    b.add_edge("audit", END)
    return b.compile(checkpointer=deps.checkpointer)


# ── Hydration / assembly ─────────────────────────────────────────────────────

def _trim_history(history: list[dict], max_turns: int, max_chars: int) -> list[dict]:
    """Server-side defensive re-trim (§5.1): never trust the client to bound it."""
    turns = history[-max_turns:] if max_turns > 0 else history
    total, kept = 0, []
    for t in reversed(turns):
        c = len(t.get("content", "") or "")
        if kept and total + c > max_chars:
            break
        total += c
        kept.append(t)
    return list(reversed(kept))


def _hydrate(req: ChatRequest, settings: Settings) -> SessionState:
    st = new_state()
    st["session_id"] = req.session_id or f"sess-{uuid.uuid4().hex[:12]}"
    st["trace_id"] = f"trace-{uuid.uuid4().hex}"
    st["channel"] = req.channel
    st["application_id"] = req.application_id
    st["message"] = req.message

    hist = [{"role": t.role, "content": t.content} for t in req.context.history]
    st["history"] = _trim_history(hist, settings.history_max_turns, settings.history_max_chars)

    cs = req.context.state
    st["client_state"] = {"stage": cs.stage, "collected_slots": dict(cs.collected_slots),
                          "last_intent": cs.last_intent}
    st["slots"] = dict(cs.collected_slots)          # untrusted convenience cache (§5.1)
    st["stage"] = cs.stage
    st["last_intent"] = cs.last_intent
    st["awaiting_clarification"] = (cs.stage == "clarifying")
    return st


def _ui(action: Optional[dict]) -> UiAction:
    action = action or {}
    t = action.get("type", "none")
    if t not in _UI_TYPES:
        t = "none"
    return UiAction(type=t, payload=action.get("payload", {}) or {})


def _handoff(block: Optional[dict]) -> HandoffBlock:
    block = block or {}
    contact = block.get("contact")
    hc = None
    if contact:
        hc = HandoffContact(
            region=contact.get("region"), employee=contact.get("employee"),
            email=contact.get("email"), phone=contact.get("phone"), hours=contact.get("hours"),
        )
    return HandoffBlock(required=bool(block.get("required")), reason=block.get("reason"), contact=hc)


def _assemble(final: dict, taxonomy=None) -> ChatResponse:
    intent = final.get("intent", {}) or {}
    guard = final.get("guardrail", {}) or {}
    primary_id = intent.get("primary")
    row = taxonomy.get(primary_id) if (taxonomy and primary_id) else None
    return ChatResponse(
        session_id=final["session_id"],
        reply=final.get("reply", ""),
        intent=IntentBlock(primary=primary_id,
                           confidence=float(intent.get("confidence") or 0.0),
                           secondary=intent.get("secondary"),
                           category=(row.category if row else None),
                           definition=(row.definition if row else None),
                           type=(row.type if row else None)),
        ui_action=_ui(final.get("ui_action")),
        citations=[Citation(**c) for c in final.get("citations", [])],
        handoff=_handoff(final.get("handoff")),
        state=ResponseState(stage=final.get("stage"),
                            collected_slots=final.get("slots", {}) or {},
                            last_intent=intent.get("primary") or final.get("last_intent")),
        audit=AuditBlock(
            trace_id=final.get("trace_id", ""),
            route=final.get("route", ""),
            action=getattr(final.get("decision"), "action", None),
            rule_version=final.get("rule_version", ""),
            guardrail=GuardrailVerdict(flagged=bool(guard.get("flagged")), category=guard.get("category")),
            decision_inputs=pii.redact_obj(final.get("decision_inputs", {}) or {}),
            timestamp=final.get("timestamp", ""),
        ),
    )


def _assemble_document(res: dict, deps: Deps, *, session_id: Optional[str],
                       template_id: str, channel: str) -> ChatResponse:
    """Build the envelope for the document-upload eligibility path and write one
    audit record (outcomes only — never the raw figures)."""
    sid = session_id or f"sess-{uuid.uuid4().hex[:12]}"
    trace_id = f"trace-{uuid.uuid4().hex}"
    ts = datetime.now(timezone.utc).isoformat()
    route = "5.0 In-principle eligibility (document)"

    decision_inputs = dict(res.get("decision_inputs", {}))
    decision_inputs["source"] = "document"
    decision_inputs["template_id"] = template_id
    decision_inputs["filled_from_document"] = res.get("filled_from_document", [])

    deps.audit.write({
        "trace_id": trace_id, "session_id": sid, "channel": channel,
        "route": route, "rule_version": deps.config.rule_version,
        "guardrail": {"flagged": False, "category": None},
        "intent": {"primary": None, "confidence": None, "secondary": None},
        "decision_inputs": decision_inputs,
        "handoff_required": False, "timestamp": ts,
    })

    return ChatResponse(
        session_id=sid,
        reply=res.get("reply", ""),
        ui_action=_ui(res.get("ui_action")),
        state=ResponseState(stage=res.get("stage"),
                            collected_slots=res.get("slots", {}) or {}, last_intent=None),
        audit=AuditBlock(
            trace_id=trace_id, route=route, action="dispatch",
            rule_version=deps.config.rule_version,
            guardrail=GuardrailVerdict(flagged=False, category=None),
            decision_inputs=pii.redact_obj(decision_inputs),
            timestamp=ts,
        ),
    )


class Orchestrator:
    def __init__(self, deps: Deps):
        self.deps = deps
        self.graph = build_graph(deps)

    def handle(self, req: ChatRequest) -> ChatResponse:
        state = _hydrate(req, self.deps.settings)
        # thread_id only matters when a checkpointer is attached; with the
        # default stateless config there is nothing to key or persist.
        config = {"configurable": {"thread_id": state["session_id"]}} if self.deps.checkpointer else None
        final = self.graph.invoke(state, config=config)
        return _assemble(final, self.deps.config.taxonomy)

    def handle_document(
        self,
        *,
        template_id: str,
        file_parts: list,
        session_id: Optional[str] = None,
        application_id: Optional[str] = None,
        channel: str = "customer",
        collected_slots: Optional[dict] = None,
    ) -> ChatResponse:
        """Document-upload eligibility path: call the extraction service, map its
        output to Tier-1 slots, then run the same eligibility flow. Bypasses the
        NLU graph on purpose — an uploaded document goes straight to eligibility
        (no classification / guardrail needed)."""
        extracted = self.deps.extraction.extract(template_id, file_parts)
        res = self.deps.eligibility.ingest_document(template_id, extracted, collected_slots or {})
        return _assemble_document(res, self.deps, session_id=session_id, template_id=template_id, channel=channel)


def build_orchestrator(settings: Optional[Settings] = None) -> Orchestrator:
    return Orchestrator(build_deps(settings))
