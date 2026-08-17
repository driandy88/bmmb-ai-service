"""
FastAPI app for chat-v2 — the agentic variant of the SME Financing assistant.

Runs as its own Cloud Run service beside chat-service. It shares that service's
config, corpus and response wording (vendored, byte-identical, drift-tested) so
an A/B between them measures the APPROACH and nothing else.

Startup builds the agent once: the Vertex model client, the compiled tool loop,
and the system prompt assembled from app/policy/*.md.
"""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config.settings import get_settings
from app.config.settings_v2 import get_v2_settings
from app.utils.logging import get_logger

log = get_logger("main")
_settings = get_settings()
_v2 = get_v2_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build eagerly so a bad model config fails at deploy, not on a customer's
    # first message.
    from app.runtime.agent import get_agent
    from app.policy.loader import system_prompt

    try:
        get_agent()
        log.info("Agent ready (model=%s, rag=%s, max_steps=%d, prompt=%d chars).",
                 _settings.model_id, _settings.rag_backend, _v2.agent_max_steps,
                 len(system_prompt()))
    except Exception as exc:  # noqa: BLE001 — /health reports it; don't crash the container
        log.error("Agent failed to build at startup (%s): %s", type(exc).__name__, exc)
    yield


app = FastAPI(
    title="SME Financing — Customer Service Agent (v2, agentic)",
    description="Experimental agentic variant of the BMMB SME financing assistant. "
                "Same policy, corpus and response wording as chat-service; the "
                "difference is that a tool-calling agent plans the turn instead of "
                "a fixed graph routing it.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.origins_list(),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root():
    return {
        "message": "SME Financing Customer Service Agent v2 (agentic) is running.",
        "docs": "/docs",
        "policy": "/v2/policy",
    }