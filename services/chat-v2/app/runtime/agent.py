"""
The agent loop — v2's replacement for v1's LangGraph state machine.

v1: guardrail -> classify -> decide -> {refuse|clarify|handoff|dispatch|canned}
    -> terminology -> audit. Ten nodes, a precedence ladder, and a stage table
    the client has to echo back.

v2: guardrail -> agent(tools) -> terminology -> audit. The agent decides what to
    do and in what order; the deterministic bookends stay exactly where they were.

Two things are deliberately NOT the model's job, same as v1:

  * the denylist runs first and can refuse without spending a token — a flagged
    turn costs zero LLM calls here (v1 still pays for its classify step)
  * terminology lint and audit run after, on every path, no exceptions

`langgraph` appears in v2's dependencies only for `create_react_agent`. There is
no hand-authored graph.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from app.config.settings import Settings, get_settings
from app.config.settings_v2 import V2Settings, get_v2_settings
from app.policy.loader import system_prompt
from app.runtime import guardrail
from app.runtime.context import TurnContext, begin_turn
from app.tools import ALL_TOOLS
from app.utils.logging import get_logger

log = get_logger("agent")


def _trim(history: list[dict], max_turns: int, max_chars: int) -> list[dict]:
    """Server-side re-trim, ported from v1's _trim_history: never trust the
    client to bound what it sends."""
    turns = history[-max_turns:] if max_turns > 0 else history
    total, kept = 0, []
    for t in reversed(turns):
        c = len(t.get("content", "") or "")
        if kept and total + c > max_chars:
            break
        total += c
        kept.append(t)
    return list(reversed(kept))


def _to_messages(history: list[dict], message: str) -> list:
    msgs: list[Any] = []
    for t in history:
        content = (t.get("content") or "").strip()
        if not content:
            continue
        role = (t.get("role") or "").lower()
        msgs.append(AIMessage(content=content) if role in ("assistant", "ai", "bot")
                    else HumanMessage(content=content))
    msgs.append(HumanMessage(content=message))
    return msgs


class Agent:
    """Built once at startup and reused — the model client, the compiled loop and
    the assembled system prompt are all expensive to rebuild per request."""

    def __init__(self, settings: Optional[Settings] = None, v2: Optional[V2Settings] = None):
        self.settings = settings or get_settings()
        self.v2 = v2 or get_v2_settings()
        self._prompt = system_prompt()
        self._graph = create_react_agent(self._build_model(), ALL_TOOLS, prompt=self._prompt)

    def _build_model(self):
        from langchain_google_vertexai import ChatVertexAI

        return ChatVertexAI(
            model_name=self.settings.model_id,
            project=self.settings.gcp_project_id,
            location=self.settings.vertex_location,
            temperature=self.v2.temperature,
            max_retries=2,
        )

    # ── one turn ─────────────────────────────────────────────────────────────

    def run(self, message: str, history: list[dict], *, channel: str = "customer") -> dict:
        """-> {reply, ctx, guardrail, steps, capped}

        `ctx` carries everything the tools recorded (citations, ui_action,
        handoff, suggestions) for the caller to assemble into the envelope.
        """
        ctx = begin_turn()

        # 1. Deterministic screen. A hit refuses without reaching the model.
        verdict = guardrail.screen(message)
        if verdict.flagged:
            from app.config.loader import load_config
            reply = load_config().responses.wording(verdict.response_ref or "R6")
            ctx.record("guardrail", category=verdict.category, source="denylist")
            log.info("refused pre-agent (%s)", verdict.category)
            return {"reply": reply, "ctx": ctx, "guardrail": verdict, "steps": 0, "capped": False}

        # 2. The agent turn.
        trimmed = _trim(history, self.settings.history_max_turns, self.settings.history_max_chars)
        state = {"messages": _to_messages(trimmed, message)}

        # LangGraph counts every node visit, and one tool call is two visits
        # (model, then tool). +1 for the closing model turn that writes the reply.
        config = {"recursion_limit": self.v2.agent_max_steps * 2 + 1}

        capped = False
        try:
            out = self._graph.invoke(state, config=config)
            msgs = out["messages"]
        except GraphRecursionError:
            # Return what the tools already produced rather than erroring at the
            # customer. A rising count here means a prompt is sending the model
            # in circles.
            capped = True
            msgs = []
            log.warning("agent hit the step cap (%d)", self.v2.agent_max_steps)
        except Exception as exc:
            log.error("agent turn failed (%s)", type(exc).__name__)
            return {
                "reply": "Sorry — I'm having trouble right now. Our SME financing team can help "
                         "in the meantime, or try again in a moment.",
                "ctx": ctx, "guardrail": verdict, "steps": 0, "capped": False, "error": type(exc).__name__,
            }

        reply = ""
        for m in reversed(msgs):
            if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
                reply = m.content.strip()
                break

        if not reply:
            reply = ("Sorry — I couldn't put that together. Would you like me to connect you "
                     "with our SME financing team?")

        steps = sum(1 for m in msgs if isinstance(m, (AIMessage, ToolMessage)))
        return {"reply": reply, "ctx": ctx, "guardrail": verdict, "steps": steps, "capped": capped}


_AGENT: Optional[Agent] = None


def get_agent() -> Agent:
    global _AGENT
    if _AGENT is None:
        _AGENT = Agent()
    return _AGENT