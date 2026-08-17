"""
Per-turn side channel for tool effects.

A LangChain tool returns a string — that string goes back to the model, and the
model writes prose from it. But the HTTP envelope needs more than prose: the
citation list, a `ui_action` for the frontend to render a card or open a link,
the resolved handoff contact, next-step chips, updated slots.

Threading all of that through tool return values would mean the model reading
JSON it has no use for, and us parsing prose to recover structure. Instead each
tool writes its structured effect here and returns only what the model needs to
write a good sentence.

Scoped with a ContextVar so concurrent requests never share state — the app is
sync under a threadpool, and a module-level dict would leak between turns.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

_NONE_UI = {"type": "none", "payload": {}}


@dataclass
class TurnContext:
    """Everything the tools produced this turn, for assembling the response."""

    # Accumulated across every search in the turn, numbered from 1 and never
    # renumbered — so a citation marker the model wrote after its first search
    # still points at the right source after a second one.
    citations: list[dict[str, Any]] = field(default_factory=list)

    ui_action: dict[str, Any] = field(default_factory=lambda: dict(_NONE_UI))
    handoff: dict[str, Any] = field(default_factory=lambda: {"required": False, "reason": None, "contact": None})
    suggestions: list[dict[str, str]] = field(default_factory=list)
    slots: dict[str, Any] = field(default_factory=dict)

    # Audit trail: which tools ran, in order, and what they were asked for.
    # Arguments are recorded as short scalars only — never raw customer text.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def add_citations(self, chunks: list[Any]) -> list[int]:
        """Register chunks and return the 1-based numbers assigned to them, so
        the tool can tell the model which markers to cite."""
        from app.agents.rag.synthesize import _citation

        start = len(self.citations)
        for i, c in enumerate(chunks, start=start + 1):
            self.citations.append(_citation(i, c))
        return list(range(start + 1, len(self.citations) + 1))

    def record(self, tool: str, **args: Any) -> None:
        self.tool_calls.append({"tool": tool, **args})

    def set_ui(self, type_: str, **payload: Any) -> None:
        """First writer wins.

        The envelope carries one `ui_action`, so when a turn calls two tools that
        both want to render something (programme cards AND a contact card), one
        has to lose. v1 has the same constraint and keeps the PRIMARY handler's
        action, dropping the secondary's — so v2 keeps the first tool's rather
        than the last, matching that behaviour instead of silently inverting it.

        The suppressed action is recorded so a rising count is visible if this
        turns out to matter more often than expected.
        """
        if self.ui_action.get("type", "none") != "none":
            self.record("ui_suppressed", type=type_, kept=self.ui_action.get("type"))
            return
        self.ui_action = {"type": type_, "payload": payload}


_CURRENT: ContextVar[Optional[TurnContext]] = ContextVar("turn_context", default=None)


def begin_turn() -> TurnContext:
    ctx = TurnContext()
    _CURRENT.set(ctx)
    return ctx


def current() -> TurnContext:
    """The active turn's context.

    Falls back to a throwaway when unset so a tool called outside a request
    (a unit test, the eval harness) still works instead of raising.
    """
    ctx = _CURRENT.get()
    if ctx is None:
        ctx = TurnContext()
        _CURRENT.set(ctx)
    return ctx