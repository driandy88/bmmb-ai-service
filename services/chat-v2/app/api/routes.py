"""
HTTP surface. Paths mirror v1 under a /v2 prefix so switching the A/B is a base
path change and nothing else:

    POST /v2/chat          same ChatRequest in, same ChatResponse out
    POST /v2/chat/stream   same SSE frames (start / token / done)
    GET  /try              a minimal chat UI for testing this service directly
    GET  /v2/policy        the assembled system prompt + agent card
    GET  /health

/try exists because v2 has no frontend of its own — the portal talks to v1. It is
a test bench, not a product surface: alongside each reply it shows which tools
ran, how many steps, and whether any guard fired, which is the whole point when
comparing approaches.

/v2/policy has no v1 counterpart. v2's behaviour lives in markdown rather than a
precedence table, so being able to read what the deployed image was actually told
is the equivalent of reading v1's routing.py — and it is the first thing you want
when a turn goes wrong.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.api.schemas import ChatRequest, ChatResponse, HealthResponse
from app.config.settings import get_settings
from app.config.settings_v2 import get_v2_settings
from app.policy.loader import agent_card, system_prompt
from app.runtime import postprocess
from app.runtime.agent import get_agent

router = APIRouter()

_STREAM_TOKEN_DELAY = 0.018  # matches v1 so the typing feel is identical


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _handle(req: ChatRequest) -> ChatResponse:
    agent = get_agent()
    history = [{"role": t.role, "content": t.content} for t in req.context.history]
    result = agent.run(req.message, history, channel=req.channel)
    return postprocess.finalize(
        reply=result["reply"],
        ctx=result["ctx"],
        session_id=req.session_id,
        channel=req.channel,
        guardrail_flagged=result["guardrail"].flagged,
        guardrail_category=result["guardrail"].category,
        steps=result["steps"],
        capped=result["capped"],
        error=result.get("error"),
    )


@router.post("/v2/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    # The agent loop and the vendored retriever are both blocking, so keep them
    # off the event loop — same posture as v1's /chat/stream.
    return await asyncio.to_thread(_handle, req)


@router.post("/v2/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Identical frames to v1: the turn runs to completion, then the finished
    reply is replayed word by word. Not true token streaming in either version —
    tool-calling turns cannot emit prose until the tools have answered."""

    async def gen():
        yield _sse("start", {"session_id": req.session_id})
        try:
            resp = await asyncio.to_thread(_handle, req)
        except Exception as exc:  # noqa: BLE001 — surface in-stream, don't 500 mid-flight
            yield _sse("error", {"message": str(exc)})
            return
        for tok in re.findall(r"\s+|\S+", resp.reply):
            yield _sse("token", {"text": tok})
            if tok.strip():
                await asyncio.sleep(_STREAM_TOKEN_DELAY)
        yield _sse("done", resp.model_dump())

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


_TRY_PAGE = Path(__file__).resolve().parents[1] / "static" / "try.html"


@router.get("/try", include_in_schema=False)
def try_page():
    """Minimal chat UI for testing this service directly, without the portal."""
    return FileResponse(_TRY_PAGE, media_type="text/html")


@router.get("/v2/policy")
def policy():
    """What this deployment was actually told. Read-only introspection."""
    prompt = system_prompt()
    v2 = get_v2_settings()
    return {
        "agent_card": agent_card(),
        "system_prompt": prompt,
        "system_prompt_chars": len(prompt),
        "tools": [t.name for t in __import__("app.tools", fromlist=["ALL_TOOLS"]).ALL_TOOLS],
        "limits": {"max_steps": v2.agent_max_steps, "temperature": v2.temperature,
                   "search_top_k": v2.search_top_k},
    }


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Never throws — reports degraded so a bad config is visible rather than a 500."""
    s = get_settings()
    checks: dict[str, str] = {}
    status = "ok"

    try:
        get_agent()
        checks["agent"] = "ready"
    except Exception as exc:  # noqa: BLE001
        checks["agent"] = f"error: {exc}"
        status = "degraded"

    try:
        from app.config.loader import load_config
        load_config()
        checks["config"] = "loaded"
    except Exception as exc:  # noqa: BLE001
        checks["config"] = f"error: {exc}"
        status = "degraded"

    try:
        checks["policy"] = f"{len(system_prompt())} chars"
    except Exception as exc:  # noqa: BLE001
        checks["policy"] = f"error: {exc}"
        status = "degraded"

    checks["approach"] = "agentic (v2)"
    checks["rag_backend"] = s.rag_backend
    checks["audit_backend"] = s.audit_backend

    return HealthResponse(status=status, llm_backend="vertex", rag_backend=s.rag_backend, checks=checks)