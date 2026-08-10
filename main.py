"""
Root FastAPI app for the BMMB AI service monorepo.

Mounts all 6 per-service routers under one process / one port / one /docs,
each behind its own path prefix:

    /extraction     services.extraction  (app/main.py)
    /chat           services.chat        (app/main.py)
    /validation     services.validation  (api.py)
    /aggregation    services.aggregation (api.py)
    /bbox_generator services.bbox_generator (api.py)
    /mcp            services.mcp         (api.py)

Routing-prefix decision: each service keeps its routes exactly as it defines
them -- including its own `GET /health` -- just mounted under its prefix, e.g.
extraction's health check becomes `GET /extraction/health`. This module adds
one more, root-level `GET /health` as an aggregate liveness check.

FastAPI supports exactly one lifespan per app. Of the 6, only chat needs
startup wiring: it builds the LangGraph orchestrator once and caches it on
`app.state` (see services/chat/app/main.py). `app.include_router()` doesn't
create a separate ASGI app, so chat's handlers -- which look up the
orchestrator via `get_orchestrator(request.app)` -- see exactly the same
`app.state` this root app's lifespan populates. So we just reuse chat's
lifespan as-is for the root app; nothing else needs one.

Run locally:
    uvicorn main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.aggregation.api import router as aggregation_router
from services.bbox_generator.api import router as bbox_generator_router
from services.chat.app.config.settings import get_settings as get_chat_settings
from services.chat.app.main import lifespan as chat_lifespan
from services.chat.app.main import router as chat_router
from services.extraction.app.main import ALLOWED_ORIGINS as _extraction_origins
from services.extraction.app.main import router as extraction_router
from services.mcp.api import router as mcp_router
from services.validation.api import router as validation_router

app = FastAPI(
    title="BMMB Unified AI Service",
    description="Extraction, chat, validation, aggregation, bbox generation "
                "and MCP, mounted behind one process. See each prefix's own "
                "/docs section for details.",
    version="1.0.0",
    lifespan=chat_lifespan,
)

# Merged CORS: union of each service's own allow_origins config. Only
# extraction and chat configure CORS today -- the other four are internal
# services with no CORSMiddleware of their own, so there's nothing of theirs
# to union in. "*" from either side wins outright (it's a strict superset of
# any explicit origin list).
_chat_origins = get_chat_settings().origins_list()
if "*" in _extraction_origins or "*" in _chat_origins:
    _allow_origins = ["*"]
else:
    _allow_origins = sorted(set(_extraction_origins) | set(_chat_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# aggregation/bbox_generator/mcp/validation already tag their own router
# (APIRouter(tags=[...])) -- passing `tags=` again here would just duplicate
# that tag on every operation, so we only pass it where the router doesn't
# already carry one (extraction, chat).
app.include_router(extraction_router, prefix="/extraction", tags=["extraction"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])
app.include_router(validation_router, prefix="/validation")
app.include_router(aggregation_router, prefix="/aggregation")
app.include_router(bbox_generator_router, prefix="/bbox_generator")
app.include_router(mcp_router, prefix="/mcp")

_MOUNTED = ("extraction", "chat", "validation", "aggregation", "bbox_generator", "mcp")


@app.get("/", tags=["health"])
def root():
    return {"message": "BMMB Unified AI Service is running.", "docs": "/docs", "services": _MOUNTED}


@app.get("/health", tags=["health"])
def health():
    """Root-level aggregate liveness check -- if this process is up and
    serving, all 6 routers below are mounted and importable (a broken import
    would have failed at startup, before this was ever reachable). Each
    service also keeps its own check under its prefix (e.g. GET
    /extraction/health) for per-service probes."""
    return {"status": "ok", "services": _MOUNTED}
