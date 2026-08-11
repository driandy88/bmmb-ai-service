"""
FastAPI app for the SME Financing Customer Service agent (brief §5, §6).

Startup wiring: load env (.env locally, Cloud Run env in prod), build the
Orchestrator once (which assembles config, LLM, retriever, audit, and compiles
the LangGraph), and mount the routes.

To run standalone (build context / package root is `services`):

    uvicorn services.chat.api:app --reload

To mount the router into another service's FastAPI app instead:

    from services.chat.api import router
    app.include_router(router)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .app.api.routes import get_orchestrator, router
from .app.config.settings import get_settings
from .app.utils.logging import get_logger

log = get_logger("main")
_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the orchestrator once at startup (config + LLM + retriever + audit +
    # compiled LangGraph). get_orchestrator caches it on app.state.
    get_orchestrator(app)
    log.info("Orchestrator ready (llm=%s, rag=%s, audit=%s).",
             _settings.llm_backend, _settings.rag_backend, _settings.audit_backend)
    yield


# Standalone app, for `uvicorn services.chat.api:app`. Hosts embedding this
# elsewhere should include `router` above instead (and run this module's
# lifespan logic as part of their own).
app = FastAPI(
    title="SME Financing — Customer Service Agent",
    description="Conversational front door + orchestrator for BMMB SME financing. "
                "Classifies intent, answers in-scope questions, runs an indicative "
                "eligibility pre-check, and hands off to humans/downstream agents.",
    version="1.0.0",
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
    return {"message": "SME Financing Customer Service Agent is running.", "docs": "/docs"}
