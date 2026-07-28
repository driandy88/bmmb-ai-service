"""
Pydantic models for the `/chat` contract (brief §5).

Request  : ChatRequest  (client-supplied short memory in `context`)
Response : ChatResponse (the structured envelope the frontend consumes)

Security note (§5.1): `context` is UNTRUSTED. History roles other than
user/assistant are silently dropped here (not 422'd) so a malformed or forged
`role` can never reach the model as an instruction. All authoritative state
(taxonomy, thresholds, guardrails, eligibility) is server-side.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Channel = Literal["customer", "branch"]
UiActionType = Literal[
    "none",
    "render_eligibility_form",
    "show_eligibility_result",
    "open_application_link",
    "show_contact_card",
    "show_program_options",
]


# ── Request ──────────────────────────────────────────────────────────────────

class HistoryTurn(BaseModel):
    role: str  # validated/narrowed by ChatContext; kept as str so bad roles drop, not 422
    content: str = ""


class ClientState(BaseModel):
    """Convenience cache echoed back by the client (§5.1). The server may
    re-validate or overwrite it — it is never trusted as authoritative."""
    stage: Optional[str] = None
    collected_slots: dict[str, Any] = Field(default_factory=dict)
    last_intent: Optional[str] = None

    model_config = {"extra": "allow"}


class ChatContext(BaseModel):
    history: list[HistoryTurn] = Field(default_factory=list)
    state: ClientState = Field(default_factory=ClientState)

    @field_validator("history", mode="before")
    @classmethod
    def _drop_foreign_roles(cls, v: Any) -> Any:
        """Keep only user/assistant turns; ignore system/tool/anything else so
        a forged control turn cannot become an instruction (§5.1)."""
        if not isinstance(v, list):
            return v
        kept = []
        for turn in v:
            role = turn.get("role") if isinstance(turn, dict) else getattr(turn, "role", None)
            if role in ("user", "assistant"):
                kept.append(turn)
        return kept


class ChatRequest(BaseModel):
    session_id: Optional[str] = None          # null on first call; service returns one
    message: str
    channel: Channel = "customer"
    application_id: Optional[str] = None
    context: ChatContext = Field(default_factory=ChatContext)


# ── Response envelope ────────────────────────────────────────────────────────

class IntentBlock(BaseModel):
    primary: Optional[str] = None
    confidence: float = 0.0
    secondary: Optional[str] = None


class UiAction(BaseModel):
    type: UiActionType = "none"
    payload: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    corpus: str
    ref: str
    snippet: str


class HandoffContact(BaseModel):
    region: Optional[str] = None
    employee: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    hours: Optional[str] = None


class HandoffBlock(BaseModel):
    required: bool = False
    reason: Optional[str] = None
    contact: Optional[HandoffContact] = None


class ResponseState(BaseModel):
    """The updated short-memory state the client returns next turn."""
    stage: Optional[str] = None
    collected_slots: dict[str, Any] = Field(default_factory=dict)
    last_intent: Optional[str] = None


class GuardrailVerdict(BaseModel):
    flagged: bool = False
    category: Optional[str] = None


class AuditBlock(BaseModel):
    trace_id: str
    route: str
    action: Optional[str] = None   # routing decision: refuse|clarify|handoff|dispatch|canned (adapter hint)
    rule_version: str
    guardrail: GuardrailVerdict = Field(default_factory=GuardrailVerdict)
    decision_inputs: dict[str, Any] = Field(default_factory=dict)
    timestamp: str  # ISO-8601


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    intent: IntentBlock = Field(default_factory=IntentBlock)
    ui_action: UiAction = Field(default_factory=UiAction)
    citations: list[Citation] = Field(default_factory=list)
    handoff: HandoffBlock = Field(default_factory=HandoffBlock)
    state: ResponseState = Field(default_factory=ResponseState)
    audit: AuditBlock


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    llm_backend: str
    rag_backend: str
    checks: dict[str, str] = Field(default_factory=dict)
