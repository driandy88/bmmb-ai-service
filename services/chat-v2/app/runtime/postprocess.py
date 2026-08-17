"""
The deterministic bookend: lint the reply, write the audit record, assemble the
envelope. Runs on every path, including refusals and errors — same guarantee as
v1's terminology and audit nodes.

The envelope is v1's `ChatResponse`, unchanged, so the existing frontend renders
a v2 turn with no changes. That is what makes the A/B switchable by URL.

One translation happens here. v1's grounded answers arrive pre-split as
`sentences: [{text, cites}]` because a structured LLM call produced them that
way. v2's agent writes ordinary prose with inline `[1]` markers, so we split it
back into the same shape — the markers are stripped from the text and become
`cites`, and the frontend's chip rendering works untouched.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.api.schemas import (
    AnswerSentence, AuditBlock, ChatResponse, Citation, GuardrailVerdict, HandoffBlock,
    HandoffContact, IntentBlock, ResponseState, Suggestion, UiAction,
)
from app.integrations.audit import get_audit_writer
from app.runtime.context import TurnContext
from app.runtime import terminology_ms
from app.utils import pii, terminology

_CITE_RE = re.compile(r"\s*\[(\d+(?:\s*,\s*\d+)*)\]")
# Sentence boundary: terminator + space + capital/digit. Deliberately simple —
# over-splitting costs a chip its sentence, it does not lose the citation.
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

_UI_TYPES = {"none", "render_eligibility_form", "show_eligibility_result",
             "open_application_link", "show_contact_card", "render_contact_form",
             "show_program_options"}

_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.I)
# Trailing punctuation belongs to the sentence, not the URL.
_URL_TRIM = ".,;:!?"


def strip_unapproved_links(reply: str, allowed: set[str]) -> tuple[str, list[str]]:
    """Remove any URL the tools did not actually produce.

    An agent asked for the application link will sometimes write a plausible one
    from memory instead of calling `start_application` — observed in testing:
    it emitted `https://www.muamalat.com.my/sme-financing/apply-now/`, which does
    not come from config and was never returned by a tool.

    A fabricated link is worse than a missing one: it looks official, it may go
    nowhere, and in the wrong hands the same failure mode sends a customer
    somewhere hostile. Prompt rules make that unlikely; this makes it impossible.

    Same posture as the terminology lint — the model plans, code guarantees.
    Real links still reach the customer through `ui_action`, which the client
    renders as a button.
    """
    removed: list[str] = []

    def _sub(m: re.Match) -> str:
        raw = m.group(0)
        url = raw.rstrip(_URL_TRIM)
        trailing = raw[len(url):]
        if url in allowed:
            return raw
        removed.append(url)
        return trailing

    cleaned = _URL_RE.sub(_sub, reply)
    # Tidy the gaps a removal leaves behind (" : ." / doubled spaces).
    cleaned = re.sub(r"\s*[:;,]\s*(?=[.!?])", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, removed


def split_cited_sentences(reply: str, max_n: int) -> tuple[str, Optional[list[dict]]]:
    """Pull inline [n] markers out of the prose into per-sentence cite lists.

    Returns (clean_reply, sentences) — sentences is None when the reply carried
    no markers, which is the signal for "not a grounded answer".
    """
    if not _CITE_RE.search(reply or ""):
        return reply, None

    sentences: list[dict] = []
    for raw in _SENT_SPLIT_RE.split(reply.strip()):
        cites: list[int] = []
        for m in _CITE_RE.finditer(raw):
            for part in m.group(1).split(","):
                n = int(part.strip())
                # Ignore a marker pointing past what we actually retrieved —
                # a hallucinated citation must not reach the UI as a dead chip.
                if 1 <= n <= max_n and n not in cites:
                    cites.append(n)
        text = _CITE_RE.sub("", raw).strip()
        if text:
            sentences.append({"text": text, "cites": cites})

    clean = " ".join(s["text"] for s in sentences)
    return clean, (sentences or None)


def finalize(
    *,
    reply: str,
    ctx: TurnContext,
    session_id: Optional[str],
    channel: str,
    guardrail_flagged: bool,
    guardrail_category: Optional[str],
    steps: int,
    capped: bool,
    stage: Optional[str] = None,
    error: Optional[str] = None,
) -> ChatResponse:
    sid = session_id or f"sess-{uuid.uuid4().hex[:12]}"
    trace_id = f"trace-{uuid.uuid4().hex}"
    ts = datetime.now(timezone.utc).isoformat()

    # 1. Citations out of the prose, then the two compliance passes. The split
    #    runs first so neither rewrite can disturb a marker's position.
    reply, sentences = split_cited_sentences(reply, len(ctx.citations))

    # Only links a tool actually emitted may reach the customer.
    allowed_urls = {
        str(v) for v in (ctx.ui_action.get("payload") or {}).values()
        if isinstance(v, str) and v.startswith(("http://", "https://"))
    }
    reply, bad_links = strip_unapproved_links(reply, allowed_urls)

    # English first (vendored, byte-identical to v1), then Malay (v2-only — v1's
    # lint has the same gap but its file cannot be edited here; see terminology_ms).
    lint = terminology.lint(reply)
    ms = terminology_ms.lint_ms(lint.text)
    reply = ms.text

    # 2. Only keep citations the reply actually cited — an unused retrieval
    #    should not show up as a source the customer was never shown.
    used = {n for s in (sentences or []) for n in s["cites"]}
    citations = [c for c in ctx.citations if c.get("n") in used] if used else []

    route = "refused (denylist)" if guardrail_flagged else "agent"
    decision_inputs = {
        "tool_calls": ctx.tool_calls,
        "steps": steps,
        "step_cap_hit": capped,
        "citations_used": sorted(used),
    }
    if error:
        decision_inputs["error"] = error
    if lint.violations:
        decision_inputs["terminology_violations"] = lint.violations
    if ms.rewritten:
        decision_inputs["terminology_violations_ms"] = ms.rewritten
    if ms.flagged:
        # Ambiguous in Malay, so deliberately NOT rewritten — a human should read
        # these turns rather than trust an automatic fix.
        decision_inputs["terminology_review_ms"] = ms.flagged
    if bad_links:
        # Not a save to be relieved about — it means the agent wrote a link
        # instead of calling the tool that owns it. Alert on a rising count.
        decision_inputs["fabricated_links_removed"] = bad_links

    # 3. Audit. Outcomes and tool names only — never the customer's message, and
    #    never raw figures (the eligibility tool records rule outcomes, not values).
    get_audit_writer().write({
        "trace_id": trace_id,
        "session_id": sid,
        "channel": channel,
        "route": route,
        "rule_version": "agent_v2",
        "guardrail": {"flagged": guardrail_flagged, "category": guardrail_category},
        "intent": {"primary": None, "confidence": None, "secondary": None},
        "decision_inputs": pii.redact_obj(decision_inputs),
        "handoff_required": bool(ctx.handoff.get("required")),
        "timestamp": ts,
    })

    ui_type = ctx.ui_action.get("type", "none")
    if ui_type not in _UI_TYPES:
        ui_type = "none"

    # The directory row carries more fields than the envelope exposes, so filter
    # rather than splat — an extra YAML column must not 500 the turn.
    contact = ctx.handoff.get("contact")
    contact_block = (
        HandoffContact(**{k: contact.get(k) for k in ("region", "employee", "email", "phone", "hours")})
        if contact else None
    )
    return ChatResponse(
        session_id=sid,
        reply=reply,
        sentences=[AnswerSentence(**s) for s in sentences] if sentences else None,
        grounded=bool(sentences and citations),
        # v2 does not classify, so there is no cat_id to report. The block stays
        # in the envelope for shape parity; a consumer reading `primary` gets
        # null rather than a fabricated label.
        intent=IntentBlock(),
        ui_action=UiAction(type=ui_type, payload=ctx.ui_action.get("payload", {}) or {}),
        citations=[Citation(**c) for c in citations],
        suggestions=[Suggestion(**s) for s in ctx.suggestions],
        handoff=HandoffBlock(
            required=bool(ctx.handoff.get("required")),
            reason=ctx.handoff.get("reason"),
            contact=contact_block,
        ),
        state=ResponseState(stage=stage, collected_slots=ctx.slots, last_intent=None),
        audit=AuditBlock(
            trace_id=trace_id,
            route=route,
            action="refuse" if guardrail_flagged else "agent",
            rule_version="agent_v2",
            guardrail=GuardrailVerdict(flagged=guardrail_flagged, category=guardrail_category),
            decision_inputs=pii.redact_obj(decision_inputs),
            timestamp=ts,
        ),
    )